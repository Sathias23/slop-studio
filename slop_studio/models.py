"""Local model presence checks and downloads for template-declared model requirements.

Templates can declare ``model_requirements`` in their ``.meta.json`` —
filename, ComfyUI subfolder, download URL, optional sha256/size_bytes/auth.
This module provides two MCP-tool entry points:

- ``check_requirements(template_name)`` — read-only path classification:
  for each declared model, report whether ``<models_dir>/<subfolder>/<filename>``
  exists. Returns ``{present: [...], missing: [...]}``.

- ``download_models(template_name)`` — for each missing model, stream the
  download via ``httpx.AsyncClient`` to ``<target>.partial``, hash with
  ``hashlib.sha256`` while streaming, and atomic-rename on success. Cleans
  up the ``.partial`` on any failure (network error, hash mismatch, auth
  failure, etc.). Auth tokens ride in ``Authorization: Bearer <token>``
  per the entry's ``auth`` field; absent ``auth`` = no header.

Design notes:

- Path traversal is rejected at write-time by ``templates._validate_metadata``
  (``..``/``/``/``\\`` in ``filename`` or ``subfolder``), so this module
  trusts the on-disk metadata. We re-check via ``Path.relative_to`` after
  composing the target path as a defense-in-depth.
- HTTP redirects are NOT followed (``follow_redirects=False``). Templates
  must declare direct https URLs.
- "No declared requirements" (absent or empty list) is success — both
  tools return empty lists with a ``note`` field.
- ``terminal_error("auth_failed", ...)`` is returned BEFORE any network
  call when ``auth: "huggingface"|"civitai"`` is set and the corresponding
  credential is empty.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from slop_studio import templates as _templates_module
from slop_studio.config import (
    COMFYUI_MODELS_DIR,
    get_civitai_api_key,
    get_huggingface_token,
)
from slop_studio.errors import terminal_error, transient_error

logger = logging.getLogger(__name__)

_AUTH_TOKEN_ENV_VAR = {
    "huggingface": "HF_TOKEN",
    "civitai": "CIVITAI_API_KEY",
}


def _resolve_target_path(models_dir: Path, subfolder: str, filename: str) -> Path | None:
    """Return ``<models_dir>/<subfolder>/<filename>`` if it stays under ``models_dir``.

    Defense-in-depth: even though _validate_metadata rejects traversal
    sequences at write-time, we re-verify here that the composed path
    sits inside the models dir. Returns ``None`` on any escape.
    """
    candidate = (models_dir / subfolder / filename).resolve()
    try:
        candidate.relative_to(models_dir.resolve())
    except ValueError:
        return None
    return candidate


def _get_token_for_auth(auth: str) -> str:
    """Return the configured token for the given auth scheme, or empty string."""
    if auth == "huggingface":
        return get_huggingface_token()
    if auth == "civitai":
        return get_civitai_api_key()
    return ""


def _no_requirements_response(downloaded_field: str | None = None) -> dict:
    """Shared response shape for templates that declare no model requirements."""
    payload: dict[str, Any] = {
        "status": "success",
        "note": "Template declares no local model requirements.",
    }
    if downloaded_field is None:
        payload["present"] = []
        payload["missing"] = []
    else:
        payload[downloaded_field] = []
        payload["skipped"] = []
    return payload


async def _load_template_meta(template_name: str) -> dict:
    """Load template metadata via templates.get_template; pass through errors.

    Returns the meta dict on success, or the error response dict from
    get_template on failure (callers re-return it).
    """
    return await _templates_module.get_template(template_name)


_REDIRECT_STATUS_CODES = (301, 302, 303, 307, 308)
_MAX_REDIRECTS = 5


def _resolve_redirect_url(current_url: str, location: str) -> str:
    """Resolve a (possibly relative) ``Location`` header against the current URL."""
    parsed = urlparse(location)
    if parsed.scheme and parsed.netloc:
        return location
    base = urlparse(current_url)
    if location.startswith("//"):
        return f"{base.scheme}:{location}"
    if location.startswith("/"):
        return urlunparse((base.scheme, base.netloc, location, "", "", ""))
    # Relative path — replace the last path segment of base.
    base_path = base.path.rsplit("/", 1)[0] + "/" + location
    return urlunparse((base.scheme, base.netloc, base_path, "", "", ""))


def _normalize_requirements(meta: dict) -> list[dict]:
    """Return the meta's ``model_requirements`` list (or empty list)."""
    reqs = meta.get("model_requirements")
    if not isinstance(reqs, list):
        return []
    return reqs


async def check_requirements(template_name: str) -> dict:
    """Report which declared model files are present vs missing on disk.

    Looks up the template's ``model_requirements`` and classifies each entry
    by whether ``<models_dir>/<subfolder>/<filename>`` exists. Read-only —
    no network calls. Use this before ``queue_prompt`` for local templates
    the user hasn't run before.

    Returns ``{status: "success", present: [...], missing: [...]}`` where
    each list contains the original requirement dicts. Templates without
    ``model_requirements`` get ``{status: "success", present: [], missing: [],
    note: "Template declares no local model requirements."}``.
    """
    meta = await _load_template_meta(template_name)
    if meta.get("status") != "success":
        return meta

    requirements = _normalize_requirements(meta)
    if not requirements:
        return _no_requirements_response()

    models_dir = Path(COMFYUI_MODELS_DIR)
    if not models_dir.is_dir():
        return terminal_error(
            "directory_not_found",
            (
                f"ComfyUI models directory not found: {models_dir}. "
                f"Set SLOP_STUDIO_COMFYUI_DIR or SLOP_STUDIO_COMFYUI_MODELS_DIR, "
                f"or add comfyui_dir to ~/.config/slop-studio/config.toml."
            ),
        )

    present: list[dict] = []
    missing: list[dict] = []
    for entry in requirements:
        target = _resolve_target_path(models_dir, entry["subfolder"], entry["filename"])
        if target is None:
            return terminal_error(
                "invalid_inputs",
                (
                    f"model_requirements entry for {entry['filename']!r} resolves outside "
                    f"the models directory — refusing to inspect."
                ),
            )
        try:
            exists = target.is_file()
        except OSError as exc:
            return transient_error(
                "storage_error",
                f"Cannot stat {target}: {exc}",
            )
        if exists:
            present.append(entry)
        else:
            missing.append(entry)

    return {"status": "success", "present": present, "missing": missing}


async def _download_one(
    client: httpx.AsyncClient,
    entry: dict,
    target: Path,
) -> dict:
    """Stream-download a single model into ``<target>.partial``, atomic-rename.

    Returns a structured dict on success or an error response on failure.
    Always cleans up the ``.partial`` file before returning on failure.
    """
    filename = entry["filename"]
    url = entry["url"]
    declared_sha256 = entry.get("sha256")
    auth = entry.get("auth")

    # Defense-in-depth: refuse to send the request if the URL isn't https.
    # _validate_metadata enforces this at write-time, but a meta file written
    # before that validator existed could still slip through.
    if not url.startswith("https://"):
        return terminal_error(
            "invalid_inputs",
            (
                f"Refusing to download {filename!r}: url must start with 'https://' "
                f"(plain HTTP would expose auth tokens); got {url!r}"
            ),
        )

    headers: dict[str, str] = {}
    if auth in ("huggingface", "civitai"):
        token = _get_token_for_auth(auth)
        if not token:
            env_var = _AUTH_TOKEN_ENV_VAR[auth]
            return terminal_error(
                "auth_failed",
                (
                    f"Cannot download {filename!r}: {auth} auth required but no token "
                    f"configured. Set the {env_var} env var or add it to "
                    f"~/.config/slop-studio/credentials.json."
                ),
            )
        headers["Authorization"] = f"Bearer {token}"

    partial = target.parent / (target.name + ".partial")

    hasher = hashlib.sha256()
    bytes_written = 0
    try:
        target.parent.mkdir(parents=True, exist_ok=True)

        # Manual redirect loop. We keep follow_redirects=False on the client
        # so we can strip Authorization on cross-origin hops (HF/Civitai
        # 302 to a pre-signed CDN URL pattern) and reject non-https targets.
        current_url = url
        current_headers = dict(headers)
        original_host = urlparse(url).netloc
        hops = 0
        early_return: dict | None = None
        while True:
            if hops > _MAX_REDIRECTS:
                early_return = transient_error(
                    "network_error",
                    f"Too many redirects downloading {filename!r} from {url}: too many redirects",
                )
                break
            request = client.build_request("GET", current_url, headers=current_headers)
            response = await client.send(request, stream=True)
            try:
                if response.status_code in _REDIRECT_STATUS_CODES:
                    location = response.headers.get("location") or response.headers.get("Location")
                    if not location:
                        early_return = transient_error(
                            "network_error",
                            (
                                f"Redirect for {filename!r} from {current_url} missing "
                                f"Location header (HTTP {response.status_code})."
                            ),
                        )
                        break
                    next_url = _resolve_redirect_url(current_url, location)
                    next_parsed = urlparse(next_url)
                    if next_parsed.scheme != "https":
                        early_return = transient_error(
                            "network_error",
                            (f"refusing to follow redirect to non-https: {next_url} (from {current_url})"),
                        )
                        break
                    next_host = next_parsed.netloc
                    if next_host != original_host and "Authorization" in current_headers:
                        # Cross-origin hop: strip Authorization to avoid
                        # leaking the bearer token to the CDN.
                        current_headers = {k: v for k, v in current_headers.items() if k != "Authorization"}
                    current_url = next_url
                    hops += 1
                    continue

                if response.status_code in (401, 403):
                    env_var = _AUTH_TOKEN_ENV_VAR.get(auth, "")
                    hint = f" Check {env_var}." if env_var else ""
                    early_return = terminal_error(
                        "auth_failed",
                        f"Authentication failed for {filename!r} (HTTP {response.status_code}).{hint}",
                    )
                    break
                if response.status_code >= 400:
                    early_return = transient_error(
                        "network_error",
                        f"Download of {filename!r} from {current_url} failed: HTTP {response.status_code}",
                    )
                    break
                with open(partial, "wb") as fp:
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        await asyncio.to_thread(fp.write, chunk)
                        hasher.update(chunk)
                        bytes_written += len(chunk)
                break
            finally:
                await response.aclose()
        if early_return is not None:
            with suppress(OSError):
                partial.unlink(missing_ok=True)
            return early_return
    except httpx.LocalProtocolError as exc:
        with suppress(OSError):
            partial.unlink(missing_ok=True)
        return terminal_error(
            "auth_failed",
            f"Malformed credential in Authorization header for {filename!r}: {exc}",
        )
    except httpx.HTTPError as exc:
        with suppress(OSError):
            partial.unlink(missing_ok=True)
        return transient_error(
            "network_error",
            f"Network error downloading {filename!r} from {url}: {type(exc).__name__}: {exc}",
        )
    except OSError as exc:
        with suppress(OSError):
            partial.unlink(missing_ok=True)
        return transient_error(
            "storage_error",
            f"Failed to write {partial}: {exc}",
        )

    if bytes_written == 0:
        with suppress(OSError):
            partial.unlink(missing_ok=True)
        return transient_error(
            "network_error",
            f"Empty body received for {filename!r} from {url}",
        )

    declared_size = entry.get("size_bytes")
    if declared_size is not None and bytes_written != declared_size:
        with suppress(OSError):
            partial.unlink(missing_ok=True)
        return terminal_error(
            "verification_failed",
            (f"size mismatch for {filename!r}: declared {declared_size}, got {bytes_written} bytes"),
        )

    if declared_sha256 is not None:
        computed = hasher.hexdigest()
        if computed.lower() != declared_sha256.lower():
            with suppress(OSError):
                partial.unlink(missing_ok=True)
            return terminal_error(
                "verification_failed",
                (f"sha256 mismatch for {filename!r}: declared {declared_sha256}, computed {computed}."),
            )

    if target.exists():
        # Concurrent download race — keep existing, drop our .partial.
        with suppress(OSError):
            partial.unlink(missing_ok=True)
        return {
            "status": "success",
            "filename": filename,
            "subfolder": entry["subfolder"],
            "path": str(target),
            "bytes_written": bytes_written,
            "note": "target already existed at rename time (concurrent download); kept existing file.",
        }

    try:
        partial.replace(target)
    except OSError as exc:
        with suppress(OSError):
            partial.unlink(missing_ok=True)
        return transient_error(
            "storage_error",
            f"Failed to rename {partial} -> {target}: {exc}",
        )

    return {
        "status": "success",
        "filename": filename,
        "subfolder": entry["subfolder"],
        "path": str(target),
        "bytes_written": bytes_written,
    }


async def download_models(template_name: str) -> dict:
    """Download every missing model for the given template into ComfyUI's models dir.

    For each entry in ``model_requirements`` whose target file does NOT
    already exist, streams the URL into ``<target>.partial``, hashes inline,
    and atomic-renames on success. On any failure (network error, hash
    mismatch, auth failure, write failure), the ``.partial`` is deleted
    before the error is returned — no orphaned partials.

    Returns ``{status: "success", downloaded: [...], skipped: [...]}`` on
    success. ``downloaded`` lists each freshly-fetched file; ``skipped``
    lists already-present files. On the first failure, returns the
    structured error response and stops.

    Templates without ``model_requirements`` get the empty no-op success
    shape with a ``note`` field.
    """
    meta = await _load_template_meta(template_name)
    if meta.get("status") != "success":
        return meta

    requirements = _normalize_requirements(meta)
    if not requirements:
        return _no_requirements_response(downloaded_field="downloaded")

    models_dir = Path(COMFYUI_MODELS_DIR)
    if not models_dir.is_dir():
        return terminal_error(
            "directory_not_found",
            (
                f"ComfyUI models directory not found: {models_dir}. "
                f"Set SLOP_STUDIO_COMFYUI_DIR or SLOP_STUDIO_COMFYUI_MODELS_DIR, "
                f"or add comfyui_dir to ~/.config/slop-studio/config.toml."
            ),
        )

    downloaded: list[dict] = []
    skipped: list[dict] = []

    # Pre-flight: traversal sanity + auth check before any network IO.
    for entry in requirements:
        target = _resolve_target_path(models_dir, entry["subfolder"], entry["filename"])
        if target is None:
            return terminal_error(
                "invalid_inputs",
                (
                    f"model_requirements entry for {entry['filename']!r} resolves outside "
                    f"the models directory — refusing to download."
                ),
            )
        if target.is_file():
            continue
        auth = entry.get("auth")
        if auth in ("huggingface", "civitai") and not _get_token_for_auth(auth):
            env_var = _AUTH_TOKEN_ENV_VAR[auth]
            return terminal_error(
                "auth_failed",
                (
                    f"Cannot download {entry['filename']!r}: {auth} auth required but no "
                    f"token configured. Set the {env_var} env var or add it to "
                    f"~/.config/slop-studio/credentials.json."
                ),
            )

    timeout = httpx.Timeout(connect=15.0, read=60.0, write=60.0, pool=15.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for entry in requirements:
            target = _resolve_target_path(models_dir, entry["subfolder"], entry["filename"])
            # Already verified non-None above; mypy/ruff appeasement.
            assert target is not None
            if target.is_file():
                skipped.append(
                    {
                        "filename": entry["filename"],
                        "subfolder": entry["subfolder"],
                        "path": str(target),
                    }
                )
                continue

            logger.info("Downloading model %r into %s/", entry["filename"], entry["subfolder"])
            result = await _download_one(client, entry, target)
            if result.get("status") != "success":
                return result
            downloaded.append(result)

    return {"status": "success", "downloaded": downloaded, "skipped": skipped}
