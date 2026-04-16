"""Backend router — dispatches submissions and prompt_id resolutions to a backend.

Story 6.3 formalizes the prefix contract that Story 6.2 deferred:

- ``route_submission`` emits ``"<backend>:<native>"`` prompt_ids on success.
- ``route_for_prompt_id`` strips a known prefix, resolves absent prefixes to the
  default backend (``"local"``), and raises ``ValueError`` for unknown prefixes.
- ``check_next_job`` / ``get_image`` catch that ``ValueError`` at the tool-facing
  boundary and convert it to a ``terminal_error("invalid_inputs", ...)`` — an
  unknown-prefix id is a malformed user input, not a crash.

Subsequent stories:

- Story 6.4: ``CloudBackend`` joins the registry behind an env flag; routing
  decisions start consulting template metadata + user default + override.
  ``route_for_prompt_id("cloud:...")`` stops raising automatically once the
  registry learns about ``"cloud"`` — no refactor required here.

The router deliberately calls the module-level orchestrators in
``slop_studio.backends.local`` via attribute access (``_local.queue_prompt(...)``)
rather than importing the functions by name. This keeps existing
``slop_studio.comfyui.queue_prompt`` test patches effective: the
``_ForwardingShim`` in ``slop_studio/comfyui.py`` mirrors attribute writes onto
``backends.local``, so attribute-access dispatch resolves against the patched
binding at call time.
"""

from slop_studio.backends import local as _local
from slop_studio.backends.base import Backend
from slop_studio.backends.local import LocalBackend
from slop_studio.errors import terminal_error

# Module-level singleton registry. Callers of ``get_backend("local")`` always
# receive the same instance; future stateful backends (e.g. CloudBackend with a
# cached httpx.AsyncClient in Story 6.4) depend on this.
_BACKENDS: dict[str, Backend] = {"local": LocalBackend()}

# Absent-prefix resolution target (backwards-compat for pre-6.3 callers holding
# bare native ids). Story 6.4 keeps this as ``"local"`` — the "cloud" choice is
# per-submission, encoded in the prefix going forward.
DEFAULT_BACKEND_NAME = "local"


def get_backend(name: str) -> Backend:
    """Return the registered backend by name; raise ValueError on unknown names."""
    try:
        return _BACKENDS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown backend '{name}'") from exc


def _prefix(backend_name: str, native_id: str) -> str:
    """Format a router-visible prompt_id as ``"<backend>:<native>"``.

    Exists as a helper (not inline f-strings) so Story 6.4's cloud submission
    path can reuse the exact same formatter.
    """
    return f"{backend_name}:{native_id}"


def route_for_prompt_id(prompt_id: str) -> tuple[Backend, str]:
    """Resolve a prompt_id to ``(backend, native_id)``.

    Parses a ``"<backend>:<native>"`` prefix:

    - Known prefix: return ``(backend, native)`` where ``native`` is the
      post-colon substring (split on the FIRST colon only — native ids may
      themselves contain colons).
    - No colon: treat as a legacy/bare native id and route to the default
      backend (currently ``"local"``). This is the NFR-C4 backwards-compat
      path for callers that still hold pre-6.3 ids.
    - Unknown prefix (including empty prefix from a leading ``":"``): raise
      ``ValueError``. The tool-facing orchestrators below catch this and
      convert it to a ``terminal_error`` — an unknown-prefix id is malformed
      user input, not a crash.
    """
    if ":" in prompt_id:
        prefix, native = prompt_id.split(":", 1)
        backend = _BACKENDS.get(prefix)
        if backend is None:
            raise ValueError(f"Unknown backend prefix '{prefix}' in prompt_id '{prompt_id}'")
        return backend, native
    return _BACKENDS[DEFAULT_BACKEND_NAME], prompt_id


async def route_submission(
    template_name: str,
    inputs: dict,
    aspect_ratio: str | None = None,
) -> dict:
    """Dispatch a ``queue_prompt`` submission and emit a prefixed prompt_id.

    Story 6.4 will replace the hard-coded ``"local"`` selection with template-
    meta-driven routing (see research doc §"Routing Decision Point"). The
    prefix-emission shape stays the same — only the selected backend changes.
    """
    # Intentional: delegate to the existing module-level orchestrator in
    # backends.local rather than backend.submit(). queue_prompt does full
    # template loading + input injection + seed randomization + submit; the
    # ABC's submit() only covers the final HTTP round-trip. Splitting the
    # orchestrator apart is post-Epic-6 scope.
    result = await _local.queue_prompt(template_name, inputs, aspect_ratio)
    if result.get("status") != "success" or "prompt_id" not in result:
        # Error passthrough — terminal / transient error dicts have no
        # prompt_id to prefix. Never fabricate one.
        return result
    return {**result, "prompt_id": _prefix("local", result["prompt_id"])}


async def check_next_job(prompt_ids: list[str], wait: int = 0) -> dict:
    """Poll jobs across backends. Local-only in this PR (Story 6.4 adds cloud).

    Strips each caller-supplied prefix, forwards native ids to the local
    orchestrator, and re-prefixes every id in the response back to the
    router-visible ``"local:<native>"`` form before returning. Unknown
    prefixes surface as ``terminal_error("invalid_inputs", ...)``.
    """
    if not prompt_ids:
        return terminal_error("invalid_input", "prompt_ids list is empty")

    try:
        resolved = [route_for_prompt_id(pid) for pid in prompt_ids]
    except ValueError as exc:
        return terminal_error("invalid_inputs", str(exc))

    # In this PR every entry resolves to local (Story 6.4 adds per-backend
    # fan-out). We still walk the resolved list so future mixed-backend
    # dispatch has the shape already in place.
    native_ids = [native for _backend, native in resolved]

    result = await _local.check_next_job(native_ids, wait)

    if result.get("status") == "error":
        return result

    # Canonicalize: every id that reaches the caller carries the ``"local:"``
    # prefix, regardless of whether they passed it in (bare backwards-compat
    # input is upgraded on the way out). See Dev Notes §"Re-prefix strategy".
    completed = [{**entry, "prompt_id": _prefix("local", entry["prompt_id"])} for entry in result.get("completed", [])]
    failed = [{**entry, "prompt_id": _prefix("local", entry["prompt_id"])} for entry in result.get("failed", [])]
    remaining = [_prefix("local", native) for native in result.get("remaining", [])]

    return {**result, "completed": completed, "failed": failed, "remaining": remaining}


async def get_image(prompt_id: str, *, include_base64: bool = False) -> dict | list:
    """Retrieve image output. Local-only in this PR (Story 6.4 adds cloud).

    Strips the caller-supplied prefix, forwards the native id to the local
    orchestrator, and re-prefixes the response's ``prompt_id`` field back
    to ``"local:<native>"``. Unknown prefixes surface as
    ``terminal_error("invalid_inputs", ...)``.
    """
    try:
        _backend, native_id = route_for_prompt_id(prompt_id)
    except ValueError as exc:
        return terminal_error("invalid_inputs", str(exc))

    result = await _local.get_image(native_id, include_base64=include_base64)

    # List return path is a type-hint leftover; the local orchestrator only
    # returns dict in practice. Defensive passthrough just in case.
    if not isinstance(result, dict):
        return result

    if "prompt_id" not in result:
        # Error dict — no prompt_id to re-prefix. Return verbatim.
        return result

    return {**result, "prompt_id": _prefix("local", native_id)}
