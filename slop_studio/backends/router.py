"""Backend router — dispatches submissions and prompt_id resolutions to a backend.

Story 6.3 formalized the prefix contract on ingress/egress. Story 6.4 adds
``CloudBackend`` behind an env flag, a ``backend_override`` kwarg for
explicit routing, and moves the ``ensure_ready`` lifecycle gate into the
local-path branch (cloud submissions no longer spawn local ComfyUI).

- ``route_submission`` emits ``"<backend>:<native>"`` prompt_ids on success.
  Without ``backend_override`` it routes to local (Story 6.6 adds meta-driven
  resolution). With ``backend_override="cloud"`` and the cloud backend
  registered, it routes to cloud; if cloud isn't registered the call
  returns a ``terminal_error("invalid_inputs", ...)`` explaining how to
  enable it.
- ``route_for_prompt_id`` strips a known prefix, resolves absent prefixes
  to the default backend (``"local"``), raises ``ValueError`` for unknown
  prefixes. Now round-trips ``"cloud:<id>"`` when the flag is set.
- ``check_next_job`` partitions the resolved list by backend. Single-backend
  batches (all-local or all-cloud) route to their respective implementations.
  Mixed-backend batches return a ``terminal_error`` asking the caller to
  split the call.
- ``get_image`` branches on the resolved backend's ``.name`` — local keeps
  the existing fast path; cloud uses ``CloudBackend.status`` + ``view`` and
  writes to the same ``OUTPUT_DIR/<YYYY-MM-DD>/<filename>`` layout.

Subsequent stories:

- Story 6.5: full env → credentials.json → config.toml resolution for the
  api key / base url. This module reads env vars directly today.
- Story 6.6: meta-driven ``backend`` resolution. ``backend_override`` is
  the seam 6.6 will compute from template metadata before calling here.
- Story 6.7: refined error-reason taxonomy (new codes + ``backend`` tag).

The router deliberately calls the module-level orchestrators in
``slop_studio.backends.local`` via attribute access (``_local.queue_prompt(...)``)
rather than importing the functions by name. This keeps existing
``slop_studio.comfyui.queue_prompt`` test patches effective.
"""

import asyncio
import copy
import json
import logging
import os
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
from slop_studio.config import OUTPUT_DIR, TEMPLATES_DIR
from slop_studio.errors import terminal_error, transient_error

logger = logging.getLogger(__name__)

# Module-level singleton registry. Callers of ``get_backend("local")`` always
# receive the same instance; CloudBackend joins the registry only when
# ``COMFY_CLOUD_API_KEY`` is set at import time.
_BACKENDS: dict[str, Backend] = {"local": LocalBackend()}

# Absent-prefix resolution target (backwards-compat for pre-6.3 callers holding
# bare native ids). Story 6.5 widens this to env-driven ``DEFAULT_BACKEND``;
# for 6.4 the default stays ``"local"`` — the "cloud" choice is per-submission,
# encoded in the prefix or requested via ``backend_override``.
DEFAULT_BACKEND_NAME = "local"

# Story 6.4 cloud-fan-out constants. Mirror the local orchestrator's values so
# the two code paths behave identically from the caller's perspective.
_CLOUD_POLL_INTERVAL = 3  # seconds between polls
_CLOUD_MAX_POLL_DURATION = 45  # cap the total wait per call
_CLOUD_MAX_FAILURE_RETRIES = 3


# Env-flag registration (Story 6.4). The import is lazy so test runs that
# never set the env var don't pay the CloudBackend import cost. Story 6.5
# replaces this block with a call into ``config.get_comfy_cloud_api_key()``.
_cloud_key = os.environ.get("COMFY_CLOUD_API_KEY", "").strip()
if _cloud_key:
    from slop_studio.backends.cloud import CloudBackend

    _cloud_url = os.environ.get("COMFY_CLOUD_URL", "https://cloud.comfy.org").strip()
    _BACKENDS["cloud"] = CloudBackend(
        api_key=_cloud_key,
        base_url=_cloud_url or "https://cloud.comfy.org",
    )


def get_backend(name: str) -> Backend:
    """Return the registered backend by name; raise ValueError on unknown names."""
    try:
        return _BACKENDS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown backend '{name}'") from exc


def _prefix(backend_name: str, native_id: str) -> str:
    """Format a router-visible prompt_id as ``"<backend>:<native>"``."""
    return f"{backend_name}:{native_id}"


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
        backend = _BACKENDS.get(prefix)
        if backend is None:
            raise ValueError(f"Unknown backend prefix '{prefix}' in prompt_id '{prompt_id}'")
        return backend, native
    return _BACKENDS[DEFAULT_BACKEND_NAME], prompt_id


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
        return terminal_error("invalid_inputs", f"Template '{template_name}' not found")

    try:
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return terminal_error(
            "invalid_inputs",
            f"Failed to read template '{template_name}': {exc}",
        )

    if not isinstance(workflow, dict):
        return terminal_error(
            "invalid_inputs",
            f"Template '{template_name}' workflow is not a JSON object",
        )

    meta_inputs = meta.get("inputs", {})
    for input_name, input_def in meta_inputs.items():
        if input_def.get("type") == "required" and input_name not in inputs:
            return terminal_error(
                "invalid_inputs",
                f"Missing required input '{input_name}': {input_def.get('description', '')}",
            )

    if aspect_ratio is not None and aspect_ratio not in meta.get("aspect_ratios", {}):
        supported = list(meta.get("aspect_ratios", {}).keys())
        return terminal_error(
            "invalid_inputs",
            f"Unsupported aspect ratio '{aspect_ratio}'. Supported: {supported}",
        )

    prepared = copy.deepcopy(workflow)
    try:
        await _inject_inputs_via_backend(prepared, meta_inputs, inputs, backend)
    except ValueError as exc:
        return terminal_error("validation", str(exc))
    except httpx.TransportError:
        return transient_error(
            "unreachable",
            f"Cannot upload asset to {backend.name} backend",
        )
    except httpx.HTTPStatusError as exc:
        return transient_error(
            "unreachable",
            f"{backend.name} asset upload returned HTTP {exc.response.status_code}",
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

    ``backend_override`` is the Story 6.4 seam for explicit cloud routing;
    Story 6.6 layers meta-driven resolution on top. ``lifecycle_manager``
    carries the ``ComfyUIManager`` through from the server handler —
    typed ``Any | None`` to avoid importing ``server.py`` (circular dep).
    The manager's ``ensure_ready()`` is only awaited on the local path
    (NFR-C6): cloud submissions must NOT spawn local ComfyUI.
    """
    chosen_name = backend_override or DEFAULT_BACKEND_NAME
    backend = _BACKENDS.get(chosen_name)
    if backend is None:
        if chosen_name == "cloud":
            return terminal_error(
                "invalid_inputs",
                "Cloud backend is not configured. Set COMFY_CLOUD_API_KEY to enable it.",
            )
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
                return transient_error(
                    "unreachable",
                    f"Cloud returned HTTP {exc.response.status_code}",
                )
            except httpx.TransportError:
                return transient_error("unreachable", "Cannot connect to cloud")
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
        return transient_error("unreachable", f"Cloud returned HTTP {exc.response.status_code}")
    except httpx.TransportError:
        return transient_error("unreachable", "Cannot connect to cloud")

    state = status_result.get("state")
    if state == "pending":
        return terminal_error("invalid_inputs", f"Job {native_id} is still pending (queued, not started)")
    if state == "running":
        return terminal_error(
            "invalid_inputs",
            f"Job {native_id} is still running. Call check_next_job with wait to poll first.",
        )
    if state == "failed":
        error_msg = status_result.get("error", "Cloud job failed")
        return terminal_error("generation_failed", error_msg)

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
        return terminal_error(
            "completed_no_output",
            f"Job {native_id} completed but produced no output images",
        )

    safe_filename = Path(filename).name
    if not safe_filename or safe_filename in (".", ".."):
        return terminal_error("completed_no_output", f"Job {native_id} produced an invalid filename")

    try:
        image_bytes = await backend.view(safe_filename, file_type="output")
    except httpx.HTTPStatusError as exc:
        return transient_error(
            "unreachable",
            f"Cloud returned HTTP {exc.response.status_code} fetching image",
        )
    except httpx.TransportError:
        return transient_error("unreachable", "Cannot connect to cloud")
    except ValueError as exc:
        return terminal_error("completed_no_output", str(exc))

    date_str = date.today().isoformat()
    date_dir = Path(OUTPUT_DIR) / date_str

    try:
        date_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return transient_error("storage_error", f"Cannot create output directory '{date_dir}': {exc}")

    output_path = date_dir / safe_filename
    if output_path.exists():
        stem = output_path.stem
        ext = output_path.suffix
        for counter in range(1, 1000):
            candidate = date_dir / f"{stem}_{counter:03d}{ext}"
            if not candidate.exists():
                output_path = candidate
                break

    try:
        await asyncio.to_thread(output_path.write_bytes, image_bytes)
    except OSError as exc:
        return transient_error("storage_error", f"Cannot write image to '{output_path}': {exc}")

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
