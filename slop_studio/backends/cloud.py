"""CloudBackend — Comfy Cloud REST API implementation of the Backend ABC.

Story 6.4 ships this behind the ``COMFY_CLOUD_API_KEY`` env flag. The ABC
contract mirrors ``LocalBackend`` (submit catches errors → dict; status/
history/view/upload_asset propagate httpx errors; router wraps at the
tool boundary).

Cloud-vs-local divergences (empirically verified by the probe spike —
see ``_bmad-output/planning-artifacts/research/technical-comfy-cloud-integration-research-2026-04-14.md``
sections A.6-A.8):

1. **Two-step status resolution** — ``/api/job/{id}/status`` returns
   terminal state only; callers must call ``/api/history/{id}`` to
   populate ``outputs``. ``status()`` handles the chain internally.
2. **302 auth-strip on /api/view** — ``X-API-Key`` LEAKS to
   ``storage.googleapis.com`` if httpx follows the redirect naively
   (httpx strips ``Authorization`` across hosts but NOT custom headers).
   ``view()`` issues a two-hop fetch with a fresh auth-less client for
   hop 2. The ``test_cloud_view_strips_auth_on_redirect`` canary guards
   this.
3. **LoadImage resolution by ``asset_hash``** — the worker resolves
   ``LoadImage.image`` by ``asset_hash``, not by display name. Submit-
   only success with ``name`` is a false positive. ``upload_asset()``
   returns the ``asset_hash`` field from ``/api/assets``.
4. **Three error body shapes** — cloud returns errors in three shapes
   (probe §A.3). ``_parse_error_body`` tries each in order.
5. **Terminal status enum is ``"success"``** — not ``"completed"``.
   ``status()`` treats both as terminal-success.
"""

import asyncio
import json
import logging
from pathlib import Path

import httpx

from slop_studio.backends.base import Backend
from slop_studio.backends.local import _verify_image
from slop_studio.errors import terminal_error, transient_error

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0


def _mask_key(key: str) -> str:
    """Mask an API key for logs/errors: ``comfyui-abc***``.

    Keys shorter than 7 characters are masked entirely — the prefix slice
    would expose the full key otherwise.
    """
    if not key:
        return ""
    if len(key) <= 7:
        return "***"
    return f"{key[:7]}***"


def _mask_key_in_text(text: str, api_key: str) -> str:
    """Replace every occurrence of ``api_key`` in ``text`` with its masked form.

    Defensive scrubbing for error bodies that echo the key — cloud's 403
    responses have historically surfaced snippets of identifying info.
    Applied to the ``preview`` string before it enters the user-visible
    error message in ``_submit_error_to_dict``.
    """
    if not api_key or not text:
        return text
    return text.replace(api_key, _mask_key(api_key))


def _parse_json_safe(response: httpx.Response) -> dict | None:
    """Parse ``response.json()`` defensively.

    Returns ``None`` when the body is not JSON or not a dict — cloud error
    bodies are sometimes non-JSON per probe §A.3.
    """
    try:
        body = response.json()
    except (json.JSONDecodeError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def _parse_error_body(body: dict | None) -> tuple[str, str]:
    """Extract a ``(code_or_type, human_message)`` tuple from a cloud error body.

    Tries the three observed shapes in order:

    - Shape 2: ``{"error": {"type": "VALIDATION_ERROR", "message": "..."}}``
    - Shape 3: ``{"code": "NOT_FOUND", "message": "..."}``
    - Shape 1: ``{"message": "Unmarshal type error: ..."}`` (terse)

    Returns ``("UNKNOWN", "")`` if the body is not a dict or ``None``. The
    parsed ``code_or_type`` is surfaced for Story 6.7's reason-code
    disambiguation; in Story 6.4 only the human message is rendered.
    """
    if not isinstance(body, dict):
        return "UNKNOWN", ""
    if isinstance(body.get("error"), dict):
        err = body["error"]
        return err.get("type", "UNKNOWN"), err.get("message", "")
    if "code" in body:
        return body["code"], body.get("message", "")
    return "UNKNOWN", body.get("message", "Unknown error")


_ACCOUNT_ISSUE_SUBSTRINGS = ("payment", "billing", "account", "subscription")


def _is_account_issue_code(code_or_type: str) -> bool:
    """Heuristic: does a 429 ``code`` / ``error.type`` field signal a payment/account issue?

    Per research doc §A.3 and probe-spike §A.6.3, 429 can mean either
    rate-limiting or payment-method-lapsed depending on cloud's internal
    classifier. We distinguish by case-insensitive substring match on the
    parsed ``code`` field — any occurrence of ``payment`` / ``billing`` /
    ``account`` / ``subscription`` → ``account_issue``; otherwise →
    ``rate_limited``. Absence of a ``code`` field falls through to
    ``rate_limited`` (probe-spike fallthrough contract).
    """
    if not code_or_type or code_or_type == "UNKNOWN":
        return False
    lowered = code_or_type.lower()
    return any(substr in lowered for substr in _ACCOUNT_ISSUE_SUBSTRINGS)


def _err(reason: str, message: str) -> dict:
    """Cloud-backend terminal error — tags with ``backend="cloud"``."""
    return terminal_error(reason, message, backend="cloud")


def _trans(reason: str, message: str) -> dict:
    """Cloud-backend transient error — tags with ``backend="cloud"``."""
    return transient_error(reason, message, backend="cloud")


class CloudBackend(Backend):
    """Backend that speaks the Comfy Cloud REST API.

    Registered in ``backends.router`` only when ``COMFY_CLOUD_API_KEY`` is
    set — the registry stays local-only otherwise. Story 6.5 layers the
    full env → credentials.json → default config chain; 6.4 reads the env
    var once at router import time.

    Holds its own auth state (``_api_key``) and base URL. No cached client —
    each call opens a short-lived ``httpx.AsyncClient`` to stay consistent
    with ``LocalBackend``'s pattern.
    """

    name = "cloud"

    def __init__(self, api_key: str, base_url: str = "https://cloud.comfy.org"):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-API-Key": api_key}

    def _client(self) -> httpx.AsyncClient:
        """Open a short-lived client with auth headers pre-applied.

        Callers use this for every endpoint except ``view()``, which has
        its own two-hop auth-stripping pattern.
        """
        return httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=self._headers)

    async def submit(self, workflow: dict) -> dict:
        """POST /api/prompt. Returns success dict or terminal/transient error.

        Error mapping (Story 6.7 taxonomy — see ``_submit_error_to_dict``
        for the full table):

        - 400/422 → ``invalid_workflow`` (terminal)
        - 401 → ``auth_failed`` (terminal; masks the api key)
        - 402 → ``no_credits`` (terminal; points to ``open_comfy_cloud_portal``)
        - 403 → ``account_issue`` (terminal; points to the platform portal)
        - 413/415 → ``invalid_inputs`` (terminal)
        - 429 → ``rate_limited`` OR ``account_issue`` (terminal; body
          ``code`` field disambiguates per ``_is_account_issue_code``)
        - 5xx / TransportError → ``unreachable`` (transient)

        All error dicts carry ``backend="cloud"`` via the module-level
        ``_err`` / ``_trans`` wrappers. No auto-fallback to local at any
        status code (NFR-C5). Never raises — per the ABC contract for
        ``submit``.
        """
        try:
            async with self._client() as client:
                response = await client.post(
                    f"{self._base_url}/api/prompt",
                    json={"prompt": workflow},
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return self._submit_error_to_dict(exc)
        except httpx.TransportError:
            return _trans(
                "unreachable",
                f"Cannot connect to cloud at {self._base_url}",
            )
        except httpx.InvalidURL:
            return _err(
                "invalid_inputs",
                "Invalid cloud base URL configured; check COMFY_CLOUD_URL",
            )

        body = _parse_json_safe(response)
        if body is None:
            return _err("invalid_workflow", "Cloud returned a non-JSON response")

        prompt_id = body.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            return _err(
                "invalid_workflow",
                f"Cloud response missing prompt_id: {str(body)[:200]}",
            )

        node_errors = body.get("node_errors")
        if node_errors:
            return _err(
                "invalid_workflow",
                f"Cloud workflow validation errors: {node_errors}",
            )

        return {"status": "success", "prompt_id": prompt_id}

    def _submit_error_to_dict(self, exc: httpx.HTTPStatusError) -> dict:
        """Map a submit-time HTTPStatusError to a terminal/transient error dict.

        Story 6.7 taxonomy (see ``slop_studio.errors`` for reason codes):

        - 400 → ``invalid_workflow``
        - 401 → ``auth_failed`` (masks the api key in the message)
        - 402 → ``no_credits`` (points to ``open_comfy_cloud_portal``)
        - 403 → ``account_issue`` (points to the platform portal)
        - 413, 415 → ``invalid_inputs``
        - 422 → ``invalid_workflow``
        - 429 → ``rate_limited`` OR ``account_issue`` (per body ``code`` disambiguation)
        - 5xx → ``transient_error("unreachable")``

        No auto-fallback to local at any status code (NFR-C5). All dicts
        are tagged ``backend="cloud"`` via the ``_err`` / ``_trans``
        wrappers.
        """
        status = exc.response.status_code
        body = _parse_json_safe(exc.response)
        code_or_type, message = _parse_error_body(body)
        preview_raw = message or exc.response.text[:500]
        preview = _mask_key_in_text(preview_raw, self._api_key)

        if status == 401:
            return _err(
                "auth_failed",
                (
                    f"Comfy Cloud authentication failed for key {_mask_key(self._api_key)}. "
                    "Verify COMFY_CLOUD_API_KEY or the credentials.json entry at "
                    "~/.config/slop-studio/credentials.json. Regenerate at "
                    "https://platform.comfy.org/profile/api-keys."
                ),
            )
        if status == 402:
            return _err(
                "no_credits",
                (
                    "Comfy Cloud submission rejected: insufficient credits. "
                    "Call open_comfy_cloud_portal to top up, or visit "
                    f"https://platform.comfy.org/ directly. {preview[:200]}"
                ),
            )
        if status == 403:
            return _err(
                "account_issue",
                (
                    f"Comfy Cloud account issue: {preview[:200]}. "
                    "Visit https://platform.comfy.org/ to check account status "
                    "or call open_comfy_cloud_portal."
                ),
            )
        if status == 413:
            return _err("invalid_inputs", "Payload too large")
        if status == 415:
            return _err("invalid_inputs", "Unsupported media type")
        if status == 422:
            return _err("invalid_workflow", preview[:200] or "Cloud validation failed")
        if status == 429:
            if _is_account_issue_code(code_or_type):
                return _err(
                    "account_issue",
                    (
                        f"Comfy Cloud account issue: {preview[:200]}. "
                        "Visit https://platform.comfy.org/ to check account status "
                        "or call open_comfy_cloud_portal."
                    ),
                )
            return _err(
                "rate_limited",
                f"Comfy Cloud rate-limited: {preview[:200]}. Retry after the cloud's cooldown period.",
            )
        if status == 400:
            return _err("invalid_workflow", preview[:200] or "Cloud rejected the workflow")
        if status >= 500:
            return _trans(
                "unreachable",
                f"Cloud returned {status} at {self._base_url}",
            )
        return _err("invalid_workflow", f"Cloud returned HTTP {status}: {preview[:200]}")

    async def status(self, prompt_id: str) -> dict:
        """GET /api/job/{prompt_id}/status. Returns unified state dict.

        Synthesizes the ABC's unified shape by mapping the cloud status
        string through the probe-spike §A.6.2 table:

        - ``pending`` / ``waiting_to_dispatch`` → ``pending``
        - ``in_progress`` / ``executing`` → ``running``
        - ``success`` / ``completed`` → ``completed`` (calls ``history()`` to
          populate ``outputs``)
        - ``error`` → ``failed`` (puts ``error_message`` into ``error``)
        - ``cancelled`` → ``failed`` with ``error: "cancelled"``

        Propagates ``httpx.HTTPStatusError`` / ``httpx.TransportError`` — the
        router's orchestration layer wraps at the tool boundary.
        """
        async with self._client() as client:
            response = await client.get(f"{self._base_url}/api/job/{prompt_id}/status")
            response.raise_for_status()

        body = _parse_json_safe(response) or {}
        status_str = body.get("status") or body.get("state") or ""

        if status_str in ("pending", "waiting_to_dispatch"):
            return {"state": "pending"}
        if status_str in ("in_progress", "executing"):
            return {"state": "running"}
        if status_str in ("success", "completed"):
            outputs = await self.history(prompt_id)
            return {"state": "completed", "outputs": outputs}
        if status_str == "error":
            return {
                "state": "failed",
                "error": body.get("error_message") or "Cloud job failed",
            }
        if status_str == "cancelled":
            return {"state": "failed", "error": "cancelled"}
        return {"state": "running"}

    async def history(self, prompt_id: str) -> dict:
        """GET /api/history/{prompt_id}. Returns the outputs dict.

        Note: the research doc guessed ``/api/history_v2`` but probe §A.7
        item 4 confirmed ``/api/history/{id}`` is the working path.

        Response envelope: ``{"history": [{"prompt_id": ..., "outputs": ...}]}``.
        Returns ``{}`` for in-flight jobs (no matching entry) — matches
        ``LocalBackend.history``'s contract.
        """
        async with self._client() as client:
            response = await client.get(f"{self._base_url}/api/history/{prompt_id}")
            response.raise_for_status()

        body = _parse_json_safe(response) or {}
        entries = body.get("history")
        if not isinstance(entries, list):
            return {}
        for entry in entries:
            if isinstance(entry, dict) and entry.get("prompt_id") == prompt_id:
                outputs = entry.get("outputs", {})
                return outputs if isinstance(outputs, dict) else {}
        return {}

    async def view(self, filename: str, subfolder: str = "", file_type: str = "output") -> bytes:
        """GET /api/view with two-hop fetch — strips auth on the redirect.

        Security rationale (probe §A.6.2): httpx strips ``Authorization``
        across cross-host redirects but NOT custom headers like
        ``X-API-Key``. Naive ``follow_redirects=True`` LEAKS the api key
        to ``storage.googleapis.com``. We issue hop 1 with
        ``follow_redirects=False`` to capture the ``Location``, then open
        a FRESH auth-less client for hop 2.

        Returns image bytes. Raises ``ValueError`` for invalid filenames;
        propagates ``httpx`` errors per the ABC.
        """
        safe_filename = Path(filename).name
        if not safe_filename or safe_filename in (".", ".."):
            raise ValueError(f"Invalid filename: {filename!r}")

        params: dict[str, str] = {"filename": safe_filename, "type": file_type}
        if subfolder:
            params["subfolder"] = subfolder

        # Hop 1: auth'd origin request, do NOT follow redirects.
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=self._headers) as client:
            response = await client.get(
                f"{self._base_url}/api/view",
                params=params,
                follow_redirects=False,
            )
            if response.status_code == 200:
                # Hypothetical direct-bytes path (no redirect). Return verbatim.
                return response.content
            if response.status_code not in (301, 302, 303, 307, 308):
                response.raise_for_status()
            location = response.headers.get("Location")
            if not location:
                raise httpx.HTTPStatusError(
                    f"Cloud /api/view returned {response.status_code} without Location header",
                    request=response.request,
                    response=response,
                )
            if not location.startswith(("https://", "http://")):
                raise httpx.HTTPStatusError(
                    f"Cloud /api/view returned relative redirect: {location!r}",
                    request=response.request,
                    response=response,
                )

        # Hop 2: FRESH client, no auth headers. Signed URLs may themselves 3xx
        # to CDN edges — follow_redirects=True is safe here (no secrets on board).
        # TODO(6.4-deferred): switch to client.stream() for large outputs.
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as redirect_client:
            follow = await redirect_client.get(location, follow_redirects=True)
            follow.raise_for_status()
            return follow.content

    async def upload_asset(self, file_path: str) -> str:
        """POST multipart to /api/assets. Returns the ``asset_hash``.

        The worker resolves ``LoadImage.image`` by ``asset_hash`` (probe
        §A.6.1 / §A.7 item 1) — not by display name. The ``/api/assets``
        response returns 201 on fresh upload, 200 on dedup-hit; both carry
        the same ``asset_hash``. Reads file bytes off the event loop via
        ``asyncio.to_thread`` (matches the ``_upload_image`` pattern in
        ``backends/local.py``).

        Raises ``ValueError`` for missing/invalid files; propagates httpx
        errors on upload failure.
        """
        path = Path(file_path)
        if not path.is_file():
            raise ValueError(f"Image file not found: {file_path}")

        try:
            await asyncio.to_thread(_verify_image, file_path)
        except Exception as exc:
            raise ValueError(f"File is not a valid image: {file_path}") from exc

        ext = path.suffix.lower() or ".png"
        mime_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
            ".bmp": "image/bmp",
        }
        mime_type = mime_types.get(ext, "application/octet-stream")
        basename = path.name

        image_bytes = await asyncio.to_thread(path.read_bytes)

        async with self._client() as client:
            response = await client.post(
                f"{self._base_url}/api/assets",
                files={"file": (basename, image_bytes, mime_type)},
                data={
                    "tags": json.dumps(["input"]),
                    "name": basename,
                    "mime_type": mime_type,
                },
            )
            response.raise_for_status()

        body = _parse_json_safe(response)
        if body is None:
            raise ValueError(f"Cloud /api/assets returned non-JSON body: {response.text[:200]}")
        asset_hash = body.get("asset_hash")
        if not isinstance(asset_hash, str) or not asset_hash:
            raise ValueError(f"Cloud /api/assets response missing asset_hash: {str(body)[:200]}")
        return asset_hash
