"""Backend router — dispatches submissions and prompt_id resolutions to a backend.

Story 6.3 formalized the prefix contract on ingress/egress. Story 6.4 adds
``CloudBackend`` behind an env flag, a ``backend_override`` kwarg for
explicit routing, and moves the ``ensure_ready`` lifecycle gate into the
local-path branch (cloud submissions no longer spawn local ComfyUI).

- ``route_submission`` emits ``"<backend>:<native>"`` prompt_ids on success.
  Without ``backend_override`` it routes to ``config.DEFAULT_BACKEND``
  (Story 6.5; Story 6.6 adds meta-driven resolution). With
  ``backend_override="cloud"`` and the cloud backend registered, it routes
  to cloud; if cloud isn't registered the call returns a
  ``terminal_error("auth_failed", ...)`` explaining how to enable it.
- ``route_for_prompt_id`` strips a known prefix, resolves absent prefixes
  to ``config.DEFAULT_BACKEND``, raises ``ValueError`` for unknown
  prefixes. Round-trips ``"cloud:<id>"`` when cloud is registered.
- ``check_next_job`` partitions the resolved list by backend. Single-backend
  batches (all-local or all-cloud) route to their respective implementations.
  Mixed-backend batches return a ``terminal_error`` asking the caller to
  split the call.
- ``get_image`` branches on the resolved backend's ``.name`` — local keeps
  the existing fast path; cloud uses ``CloudBackend.status`` + ``view`` and
  writes to the same ``OUTPUT_DIR/<YYYY-MM-DD>/<filename>`` layout.

Subsequent stories:

- Story 6.5: the api key, base URL, and default backend are resolved via
  ``slop_studio.config`` rather than raw env reads.
- Story 6.6: meta-driven ``backend`` resolution. ``backend_override``
  wins; the template's ``backend`` declaration in ``.meta.json`` is
  consulted next; ``"either"`` falls through to ``DEFAULT_BACKEND_NAME``;
  an absent field also falls through to preserve Story 6.5's user-default
  seam. Absent/unreadable meta is not fatal — downstream dispatchers
  surface the real error.
- Story 6.7: refined error-reason taxonomy. New terminal codes
  (``auth_failed``, ``no_credits``, ``account_issue``, ``rate_limited``)
  are emitted by CloudBackend + router cloud paths. Every cloud-path
  error dict is tagged ``backend="cloud"``; local-path errors are
  tagged ``backend="local"`` by ``backends.local``. Caller-input errors
  (unknown prompt_id prefix, empty list, mixed-backend batch) stay
  untagged — they precede backend resolution. NFR-C5 preserved: 429
  maps to terminal ``rate_limited`` (or ``account_issue`` on body
  disambiguation), never transient.

The router deliberately calls the module-level orchestrators in
``slop_studio.backends.local`` via attribute access (``_local.queue_prompt(...)``)
rather than importing the functions by name. This keeps existing
``slop_studio.comfyui.queue_prompt`` test patches effective.
"""

import asyncio
import copy
import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from slop_studio.backends import local as _local
from slop_studio.backends.base import Backend
from slop_studio.backends.local import (
    LocalBackend,
    _inject_resolution,
    _randomize_seeds,
    generate_thumbnail,
)
from slop_studio.config import (
    COMFY_CLOUD_URL,
    DEFAULT_BACKEND,
    OUTPUT_DIR,
    TEMPLATES_DIR,
    get_comfy_cloud_api_key,
)
from slop_studio.errors import terminal_error, transient_error

logger = logging.getLogger(__name__)

# Module-level singleton registry. ``local`` is registered eagerly; ``cloud``
# is resolved lazily via ``_resolve_cloud_backend`` so a running MCP server
# picks up a newly-added ``COMFY_CLOUD_API_KEY`` / credentials.json entry
# without a process restart.
_BACKENDS: dict[str, Backend] = {"local": LocalBackend()}

# Absent-prefix resolution target (backwards-compat for pre-6.3 callers holding
# bare native ids). Story 6.5 sourced this from ``config.DEFAULT_BACKEND``,
# which resolves env → config.toml → "local".
DEFAULT_BACKEND_NAME = DEFAULT_BACKEND

# Story 6.4 cloud-fan-out constants. Mirror the local orchestrator's values so
# the two code paths behave identically from the caller's perspective.
_CLOUD_POLL_INTERVAL = 3  # seconds between polls
_CLOUD_MAX_POLL_DURATION = 45  # cap the total wait per call
_CLOUD_MAX_FAILURE_RETRIES = 3


def _resolve_cloud_backend() -> Backend | None:
    """Return a ``CloudBackend`` instance reflecting the current config, or ``None``.

    Called on every cloud-path lookup so a newly-configured key takes effect
    without a process restart. Cached in ``_BACKENDS["cloud"]`` keyed by the
    masked key prefix so we don't construct a fresh backend (and its httpx
    pool) on every call — but the cache invalidates automatically when the
    user rotates the key.
    """
    key = get_comfy_cloud_api_key()
    if not key:
        _BACKENDS.pop("cloud", None)
        return None
    cached = _BACKENDS.get("cloud")
    if cached is not None and getattr(cached, "_api_key", None) == key:
        return cached
    from slop_studio.backends.cloud import CloudBackend

    backend = CloudBackend(api_key=key, base_url=COMFY_CLOUD_URL)
    _BACKENDS["cloud"] = backend
    return backend


def get_backend(name: str) -> Backend:
    """Return the registered backend by name; raise ValueError on unknown names.

    For ``cloud``, re-resolves from current config on every call so runtime
    credential changes are picked up without a process restart.
    """
    if name == "cloud":
        backend = _resolve_cloud_backend()
        if backend is None:
            raise ValueError("Unknown backend 'cloud'")
        return backend
    try:
        return _BACKENDS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown backend '{name}'") from exc


def _prefix(backend_name: str, native_id: str) -> str:
    """Format a router-visible prompt_id as ``"<backend>:<native>"``."""
    return f"{backend_name}:{native_id}"


def _cloud_err(reason: str, message: str) -> dict:
    """Router cloud-path terminal error — tags with ``backend="cloud"``."""
    return terminal_error(reason, message, backend="cloud")


def _cloud_trans(reason: str, message: str) -> dict:
    """Router cloud-path transient error — tags with ``backend="cloud"``."""
    return transient_error(reason, message, backend="cloud")


def _read_template_backend(template_name: str) -> str | None:
    """Return the template's declared backend, or ``None`` if absent/unreadable.

    Consulted by ``route_submission`` to honor a template's ``backend``
    field without requiring the caller to pass ``backend_override``.
    Returns one of ``"local"`` / ``"cloud"`` / ``"either"`` when the field
    is present and valid; returns ``None`` on ANY failure (missing file,
    malformed JSON, non-dict meta, absent key, invalid value). Failure
    falls through to ``DEFAULT_BACKEND_NAME`` in the caller — the
    underlying "template not found" cases are surfaced by the downstream
    dispatcher.

    Hot path: runs on every submission. No INFO-level logs; silently
    returns ``None`` on failure.
    """
    meta_path = Path(TEMPLATES_DIR) / f"{template_name}.meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(meta, dict):
        return None
    backend = meta.get("backend")
    if isinstance(backend, str) and backend in ("local", "cloud", "either"):
        return backend
    return None


def route_for_prompt_id(prompt_id: str) -> tuple[Backend, str]:
    """Resolve a prompt_id to ``(backend, native_id)``.

    Parses a ``"<backend>:<native>"`` prefix:

    - Known prefix: return ``(backend, native)`` (split on the FIRST colon).
    - No colon: treat as a legacy bare id and route to the default backend.
    - Unknown prefix: raise ``ValueError``. The tool-facing orchestrators
      catch this and convert to a ``terminal_error`` — unknown prefixes are
      malformed user input, not a crash.
    """
    if ":" in prompt_id:
        prefix, native = prompt_id.split(":", 1)
        if prefix == "cloud":
            backend = _resolve_cloud_backend()
            if backend is None:
                raise ValueError(f"Unknown backend prefix '{prefix}' in prompt_id '{prompt_id}'")
            return backend, native
        backend = _BACKENDS.get(prefix)
        if backend is None:
            raise ValueError(f"Unknown backend prefix '{prefix}' in prompt_id '{prompt_id}'")
        return backend, native
    if DEFAULT_BACKEND_NAME == "cloud":
        backend = _resolve_cloud_backend()
        if backend is None:
            raise ValueError(f"Default backend '{DEFAULT_BACKEND_NAME}' is not registered")
        return backend, prompt_id
    backend = _BACKENDS.get(DEFAULT_BACKEND_NAME)
    if backend is None:
        raise ValueError(f"Default backend '{DEFAULT_BACKEND_NAME}' is not registered")
    return backend, prompt_id


async def _inject_inputs_via_backend(
    workflow: dict,
    meta_inputs: dict,
    user_inputs: dict,
    backend: Backend,
) -> None:
    """Inject user inputs, uploading image inputs through the backend's uploader.

    Mirrors ``backends.local._inject_inputs`` but parameterized on the
    backend's ``upload_asset`` so the cloud path uses ``/api/assets`` +
    asset_hash instead of ComfyUI's ``/upload/image`` + filename.
    """
    for input_name, value in user_inputs.items():
        if input_name not in meta_inputs:
            continue
        input_def = meta_inputs[input_name]
        node_id = input_def.get("node_id")
        field = input_def.get("field")
        if not node_id or not field:
            logger.error(
                "Incomplete input definition for '%s': missing node_id or field",
                input_name,
            )
            continue
        if node_id not in workflow:
            logger.error(
                "Node '%s' referenced by input '%s' not found in workflow",
                node_id,
                input_name,
            )
            continue
        if "inputs" not in workflow[node_id]:
            logger.error("Node '%s' has no 'inputs' key in workflow", node_id)
            continue

        if input_def.get("input_type") == "image":
            value = await backend.upload_asset(value)

        workflow[node_id]["inputs"][field] = value


async def _prepare_and_submit(
    backend: Backend,
    template_name: str,
    inputs: dict,
    aspect_ratio: str | None,
) -> dict:
    """Load template, inject inputs via the backend's uploader, submit.

    Duplicates ~30 LOC of template/validation logic from
    ``_local.queue_prompt`` — consolidation is deferred to Story 6.6 when
    the router has two orchestration paths and the refactor's benefit is
    concrete. See ``deferred-work.md`` §"Deferred from: Story 6.4".
    """
    workflow_path = Path(TEMPLATES_DIR) / f"{template_name}.json"
    meta_path = Path(TEMPLATES_DIR) / f"{template_name}.meta.json"

    if not workflow_path.is_file() or not meta_path.is_file():
        return terminal_error("invalid_inputs", f"Template '{template_name}' not found", backend=backend.name)

    try:
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return terminal_error(
            "invalid_inputs",
            f"Failed to read template '{template_name}': {exc}",
            backend=backend.name,
        )

    if not isinstance(workflow, dict):
        return terminal_error(
            "invalid_inputs",
            f"Template '{template_name}' workflow is not a JSON object",
            backend=backend.name,
        )

    meta_inputs = meta.get("inputs", {})
    for input_name, input_def in meta_inputs.items():
        if input_def.get("type") == "required" and input_name not in inputs:
            return terminal_error(
                "invalid_inputs",
                f"Missing required input '{input_name}': {input_def.get('description', '')}",
                backend=backend.name,
            )

    if aspect_ratio is not None and aspect_ratio not in meta.get("aspect_ratios", {}):
        supported = list(meta.get("aspect_ratios", {}).keys())
        return terminal_error(
            "invalid_inputs",
            f"Unsupported aspect ratio '{aspect_ratio}'. Supported: {supported}",
            backend=backend.name,
        )

    prepared = copy.deepcopy(workflow)
    try:
        await _inject_inputs_via_backend(prepared, meta_inputs, inputs, backend)
    except ValueError as exc:
        return terminal_error("validation", str(exc), backend=backend.name)
    except httpx.TransportError:
        return transient_error(
            "unreachable",
            f"Cannot upload asset to {backend.name} backend",
            backend=backend.name,
        )
    except httpx.HTTPStatusError as exc:
        return transient_error(
            "unreachable",
            f"{backend.name} asset upload returned HTTP {exc.response.status_code}",
            backend=backend.name,
        )

    _randomize_seeds(prepared)
    _inject_resolution(prepared, meta, aspect_ratio)

    return await backend.submit(prepared)


async def route_submission(
    template_name: str,
    inputs: dict,
    aspect_ratio: str | None = None,
    *,
    backend_override: str | None = None,
    lifecycle_manager: Any | None = None,
) -> dict:
    """Dispatch a ``queue_prompt`` submission and emit a prefixed prompt_id.

    ``backend_override`` is the Story 6.4 seam for explicit cloud routing
    and wins over every other signal. When no override is passed, Story 6.6
    consults the template's ``backend`` declaration via
    ``_read_template_backend``: ``"local"`` / ``"cloud"`` lock to that
    backend, ``"either"`` and any missing/unreadable meta fall through to
    ``DEFAULT_BACKEND_NAME`` (preserves the Story 6.5 user-default seam).
    ``lifecycle_manager`` carries the ``ComfyUIManager`` through from the
    server handler — typed ``Any | None`` to avoid importing ``server.py``
    (circular dep). The manager's ``ensure_ready()`` is only awaited on
    the local path (NFR-C6): cloud submissions must NOT spawn local ComfyUI.
    """
    if backend_override:
        chosen_name = backend_override
    else:
        tmpl_backend = _read_template_backend(template_name)
        if tmpl_backend in ("local", "cloud"):
            chosen_name = tmpl_backend
        elif tmpl_backend == "either":
            chosen_name = DEFAULT_BACKEND_NAME
        else:
            chosen_name = DEFAULT_BACKEND_NAME
    if chosen_name == "cloud":
        backend = _resolve_cloud_backend()
        if backend is None:
            return _cloud_err(
                "auth_failed",
                "Comfy Cloud API key is not configured. Set COMFY_CLOUD_API_KEY in "
                "the environment or add a 'comfy_cloud': {'api_key': '...'} entry to "
                "~/.config/slop-studio/credentials.json. Call open_comfy_cloud_portal "
                "or visit https://platform.comfy.org/profile/api-keys to get a key.",
            )
    else:
        backend = _BACKENDS.get(chosen_name)
        if backend is None:
            return terminal_error("invalid_inputs", f"Unknown backend '{chosen_name}'")

    if backend.name == "local" and lifecycle_manager is not None:
        error = await lifecycle_manager.ensure_ready()
        if error:
            return error

    if backend.name == "local":
        # Keep the existing full orchestrator for local — test_comfyui.py's
        # 63 tests exercise queue_prompt directly and the shim depends on it.
        result = await _local.queue_prompt(template_name, inputs, aspect_ratio)
    else:
        result = await _prepare_and_submit(backend, template_name, inputs, aspect_ratio)

    if result.get("status") != "success" or "prompt_id" not in result:
        return result
    return {**result, "prompt_id": _prefix(backend.name, result["prompt_id"])}


async def _check_next_job_cloud(
    backend: Backend,
    native_ids: list[str],
    wait: int,
) -> dict:
    """Cloud equivalent of ``_local.check_next_job``.

    Mirrors the local semantics: poll each id through ``backend.status``,
    retry failed ids up to ``_CLOUD_MAX_FAILURE_RETRIES`` times, respect
    the ``effective_wait`` cap. On network errors, return a transient
    error immediately (the caller can retry the whole batch).

    Duplicates ~40 LOC from ``_local.check_next_job`` — see deferred-work
    note for consolidation plans.
    """
    effective_wait = min(wait, _CLOUD_MAX_POLL_DURATION)
    remaining = list(dict.fromkeys(native_ids))
    failure_counts: dict[str, int] = {}
    completed: list[dict] = []
    failed: list[dict] = []

    async def poll_cycle() -> dict | None:
        still_remaining = []
        for nid in list(remaining):
            try:
                result = await backend.status(nid)
            except httpx.HTTPStatusError as exc:
                # Map 401/402/403/429 through the cloud error taxonomy so
                # auth/credit/quota failures surface as terminal errors
                # instead of misleading "unreachable" transient retries.
                return backend.http_error_to_dict(exc)
            except httpx.TransportError:
                return _cloud_trans("unreachable", "Cannot connect to cloud")
            state = result.get("state")
            if state == "completed":
                completed.append({"prompt_id": nid, "outputs": result.get("outputs", {})})
            elif state == "failed":
                failure_counts[nid] = failure_counts.get(nid, 0) + 1
                if failure_counts[nid] >= _CLOUD_MAX_FAILURE_RETRIES:
                    failed.append({"prompt_id": nid, "error": result.get("error", "Job failed")})
                else:
                    still_remaining.append(nid)
            else:
                still_remaining.append(nid)
        remaining[:] = still_remaining
        return None

    err = await poll_cycle()
    if err is not None:
        return err
    if completed or failed or effective_wait <= 0:
        return {
            "status": "completed",
            "completed": completed,
            "failed": failed,
            "remaining": list(remaining),
        }

    deadline = time.monotonic() + effective_wait
    while time.monotonic() < deadline:
        await asyncio.sleep(_CLOUD_POLL_INTERVAL)
        err = await poll_cycle()
        if err is not None:
            return err
        if completed or failed:
            return {
                "status": "completed",
                "completed": completed,
                "failed": failed,
                "remaining": list(remaining),
            }

    return {
        "status": "waiting",
        "completed": [],
        "failed": [],
        "remaining": list(remaining),
    }


async def check_next_job(prompt_ids: list[str], wait: int = 0) -> dict:
    """Poll jobs across backends.

    Resolves every id to its backend, partitions by ``backend.name``, and
    delegates to the matching orchestrator. Mixed-backend batches return
    a ``terminal_error`` — true mixed fan-out is deferred.

    Re-prefixes every id that reaches the caller with the resolved
    backend's ``name`` (fixes the Story 6.3 deferred-work item that
    hardcoded ``"local"`` here).
    """
    if not prompt_ids:
        return terminal_error("invalid_inputs", "prompt_ids list is empty")

    try:
        resolved = [route_for_prompt_id(pid) for pid in prompt_ids]
    except ValueError as exc:
        return terminal_error("invalid_inputs", str(exc))

    backends_seen = {b.name for b, _ in resolved}
    if len(backends_seen) > 1:
        return terminal_error(
            "invalid_inputs",
            "Mixed-backend prompt_id batches are not yet supported; split your call by backend.",
        )

    chosen_backend_name = next(iter(backends_seen))
    native_ids = [native for _backend, native in resolved]

    if chosen_backend_name == "local":
        result = await _local.check_next_job(native_ids, wait)
    else:
        chosen_backend = _BACKENDS[chosen_backend_name]
        result = await _check_next_job_cloud(chosen_backend, native_ids, wait)

    if result.get("status") == "error":
        return result

    completed = [
        {**entry, "prompt_id": _prefix(chosen_backend_name, entry["prompt_id"])}
        for entry in result.get("completed", [])
    ]
    failed = [
        {**entry, "prompt_id": _prefix(chosen_backend_name, entry["prompt_id"])} for entry in result.get("failed", [])
    ]
    remaining = [_prefix(chosen_backend_name, native) for native in result.get("remaining", [])]

    return {**result, "completed": completed, "failed": failed, "remaining": remaining}


async def _get_image_cloud(
    backend: Backend,
    native_id: str,
    *,
    include_base64: bool,
) -> dict:
    """Cloud equivalent of ``_local.get_image``.

    Orchestrates: status → outputs extraction → view (two-hop, auth-stripped)
    → write to ``OUTPUT_DIR/<YYYY-MM-DD>/<filename>``. Error paths mirror
    ``LocalBackend.get_image``: pending/running → ``invalid_inputs``,
    failed → ``generation_failed``, missing output → ``completed_no_output``.
    """
    try:
        status_result = await backend.status(native_id)
    except httpx.HTTPStatusError as exc:
        return _cloud_trans("unreachable", f"Cloud returned HTTP {exc.response.status_code}")
    except httpx.TransportError:
        return _cloud_trans("unreachable", "Cannot connect to cloud")

    state = status_result.get("state")
    if state == "pending":
        return _cloud_err("invalid_inputs", f"Job {native_id} is still pending (queued, not started)")
    if state == "running":
        return _cloud_err(
            "invalid_inputs",
            f"Job {native_id} is still running. Call check_next_job with wait to poll first.",
        )
    if state == "failed":
        error_msg = status_result.get("error", "Cloud job failed")
        return _cloud_err("generation_failed", error_msg)

    outputs = status_result.get("outputs", {})
    filename = None
    for node_output in outputs.values():
        images = node_output.get("images", []) if isinstance(node_output, dict) else []
        if images:
            first = images[0]
            if isinstance(first, dict):
                filename = first.get("filename")
            elif isinstance(first, str):
                filename = first
            if filename:
                break

    if not filename:
        return _cloud_err(
            "completed_no_output",
            f"Job {native_id} completed but produced no output images",
        )

    safe_filename = Path(filename).name
    if not safe_filename or safe_filename in (".", ".."):
        return _cloud_err("completed_no_output", f"Job {native_id} produced an invalid filename")

    try:
        image_bytes = await backend.view(safe_filename, file_type="output")
    except httpx.HTTPStatusError as exc:
        # Route through the cloud error taxonomy — 401 here means the key
        # is dead, not that the cloud is unreachable.
        return backend.http_error_to_dict(exc)
    except httpx.TransportError:
        return _cloud_trans("unreachable", "Cannot connect to cloud")
    except ValueError as exc:
        return _cloud_err("completed_no_output", str(exc))

    date_str = date.today().isoformat()
    date_dir = Path(OUTPUT_DIR) / date_str

    try:
        date_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _cloud_trans("storage_error", f"Cannot create output directory '{date_dir}': {exc}")

    output_path = date_dir / safe_filename
    if output_path.exists():
        safe_path = Path(safe_filename)
        stem, ext = safe_path.stem, safe_path.suffix
        for counter in range(1, 1000):
            candidate = date_dir / f"{stem}_{counter:03d}{ext}"
            if not candidate.exists():
                output_path = candidate
                break

    try:
        await asyncio.to_thread(output_path.write_bytes, image_bytes)
    except OSError as exc:
        return _cloud_trans("storage_error", f"Cannot write image to '{output_path}': {exc}")

    abs_path = str(output_path.resolve())
    logger.info("Cloud image saved: %s", abs_path)

    result: dict = {
        "status": "success",
        "file_path": abs_path,
        "prompt_id": native_id,
    }

    if include_base64:
        try:
            result["thumbnail_base64"] = generate_thumbnail(image_bytes)
        except Exception:
            logger.warning("Thumbnail generation failed for %s", abs_path, exc_info=True)

    return result


async def get_image(prompt_id: str, *, include_base64: bool = False) -> dict | list:
    """Retrieve image output, routing by the resolved backend.

    Strips the prefix, dispatches to the matching backend's orchestrator,
    re-prefixes the response.
    """
    try:
        backend, native_id = route_for_prompt_id(prompt_id)
    except ValueError as exc:
        return terminal_error("invalid_inputs", str(exc))

    if backend.name == "local":
        result = await _local.get_image(native_id, include_base64=include_base64)
    else:
        result = await _get_image_cloud(backend, native_id, include_base64=include_base64)

    if not isinstance(result, dict):
        return result

    if "prompt_id" not in result:
        return result

    return {**result, "prompt_id": _prefix(backend.name, native_id)}
