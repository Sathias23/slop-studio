"""Backend router — dispatches submissions and prompt_id resolutions to a backend.

Local-only in this PR (Story 6.2). Subsequent stories:

- Story 6.3: ``route_for_prompt_id`` starts stripping ``"<backend>:"`` prefixes and
  ``route_submission`` starts emitting them.
- Story 6.4: ``CloudBackend`` joins the registry behind an env flag; routing
  decisions start consulting template metadata + user default + override.

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


def get_backend(name: str) -> Backend:
    """Return the registered backend by name; raise ValueError on unknown names."""
    try:
        return _BACKENDS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown backend '{name}'") from exc


def route_for_prompt_id(prompt_id: str) -> tuple[Backend, str]:
    """Resolve a prompt_id to ``(backend, native_id)``. Local-only in this PR.

    Story 6.3 will add prefix parsing (``"local:<uuid>"`` / ``"cloud:<uuid>"``).
    In this PR every input resolves to local regardless of shape — this both
    exercises the routing seam for Story 6.3 to modify, and preserves
    backwards-compat with legacy un-prefixed ids held by callers.
    """
    return _BACKENDS["local"], prompt_id


async def route_submission(
    template_name: str,
    inputs: dict,
    aspect_ratio: str | None = None,
) -> dict:
    """Dispatch a ``queue_prompt`` submission. Local-only in this PR.

    Story 6.4 will replace this body with template-meta-driven routing
    (see research doc §"Routing Decision Point"). For now we trivially
    select ``"local"`` but still exercise the registry lookup.
    """
    chosen = "local"
    backend = get_backend(chosen)  # raises on unknown; exercises the seam
    assert backend.name == "local"  # Story 6.4 lifts this
    # Intentional: delegate to the existing module-level orchestrator in
    # backends.local rather than backend.submit(). queue_prompt does full
    # template loading + input injection + seed randomization + submit; the
    # ABC's submit() only covers the final HTTP round-trip. Splitting the
    # orchestrator apart is Story 6.3+ scope.
    return await _local.queue_prompt(template_name, inputs, aspect_ratio)


async def check_next_job(prompt_ids: list[str], wait: int = 0) -> dict:
    """Poll jobs across backends. Local-only in this PR.

    Groups prompt_ids by backend (via route_for_prompt_id); in this PR every
    id resolves to local, so we forward the whole list to the local orchestrator.
    Story 6.3+ adds mixed-backend fan-out.
    """
    if not prompt_ids:
        return terminal_error("invalid_input", "prompt_ids list is empty")
    # Exercise the routing seam — every id resolves to local in this PR.
    for pid in prompt_ids:
        backend, _native = route_for_prompt_id(pid)
        assert backend.name == "local"  # Story 6.3+ removes this once cloud is real
    return await _local.check_next_job(prompt_ids, wait)


async def get_image(prompt_id: str, *, include_base64: bool = False) -> dict | list:
    """Retrieve image output. Local-only in this PR."""
    backend, native_id = route_for_prompt_id(prompt_id)
    assert backend.name == "local"  # Story 6.3+ lifts this
    return await _local.get_image(native_id, include_base64=include_base64)
