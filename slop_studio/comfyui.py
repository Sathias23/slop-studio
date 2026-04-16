"""Compatibility shim. Real implementation lives in slop_studio.backends.local.

This module exists so that existing imports (`from slop_studio import comfyui`
in server.py, `slop_studio.comfyui.queue_prompt` in tests) continue to work
while the implementation moves behind the Backend ABC (see Story 6.1, Epic 6).

Kept intentionally minimal — no logic here. Scheduled for deletion once all
call sites switch to the router (Story 6.2) and backends package directly.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LOAD-BEARING CONTRACT — read before refactoring any caller of backends.local
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

The ``_ForwardingShim.__setattr__`` below mirrors attribute writes on this
module to ``slop_studio.backends.local``. Test patches applied at the shim
(e.g. ``mock.patch("slop_studio.comfyui.queue_prompt", ...)``) therefore
also land on the real module. This is how ``tests/test_server.py`` keeps
working without modification across the Story 6.1 extraction and the Story
6.2 router introduction.

For that forwarding to actually take effect at runtime, callers that dispatch
into ``backends.local`` MUST use module-attribute access, NOT name imports:

    # CORRECT — attribute lookup resolves against backends.local's namespace
    # at call time, so patches applied via the shim are seen:
    from slop_studio.backends import local as _local
    await _local.queue_prompt(...)

    # WRONG — captures the pre-patch function reference at import time;
    # patches applied via the shim are invisible to this call site:
    from slop_studio.backends.local import queue_prompt
    await queue_prompt(...)

If you're adding a new caller of ``backends.local.{queue_prompt, check_job,
check_next_job, get_image, _fetch_job_status, ...}``, follow the module-attr
pattern. If you break this rule, the nearest canary is
``tests/test_server.py::test_queue_prompt_calls_ensure_ready`` — it will
fail with a real httpx connection error instead of the expected mock return.
"""

import importlib
import sys
import types

# When this shim is reloaded (tests do `importlib.reload(slop_studio.comfyui)`
# after mutating env vars to pick up fresh config), the real implementation
# module needs to be reloaded too — otherwise it keeps its original module-
# scope bindings to COMFYUI_URL / OUTPUT_DIR / TEMPLATES_DIR. On the initial
# import, backends.local is not yet in sys.modules and gets loaded by the
# from-imports below, so the guard skips this step.
if "slop_studio.backends.local" in sys.modules:
    importlib.reload(sys.modules["slop_studio.backends.local"])

# asyncio and random are re-imported at module scope so test monkeypatches
# via slop_studio.comfyui.asyncio.sleep / slop_studio.comfyui.random.randint
# still find the attributes. These patches work across modules because the
# `asyncio` and `random` modules are singletons — patching an attribute here
# propagates to every importer.
import asyncio  # noqa: F401 — re-exported for test monkeypatching
import random  # noqa: F401 — re-exported for test monkeypatching

from slop_studio.backends import local as _local
from slop_studio.backends.local import (
    DEFAULT_POLL_INTERVAL,
    MAX_FAILURE_RETRIES,
    MAX_POLL_DURATION,
    OUTPUT_DIR,
    _build_batch_result,
    _fetch_job_status,
    _format_result,
    _inject_inputs,
    _inject_resolution,
    _randomize_seeds,
    _upload_image,
    check_job,
    check_next_job,
    generate_thumbnail,
    get_image,
    queue_prompt,
)

__all__ = [
    "DEFAULT_POLL_INTERVAL",
    "MAX_FAILURE_RETRIES",
    "MAX_POLL_DURATION",
    "OUTPUT_DIR",
    "_build_batch_result",
    "_fetch_job_status",
    "_format_result",
    "_inject_inputs",
    "_inject_resolution",
    "_randomize_seeds",
    "_upload_image",
    "check_job",
    "check_next_job",
    "generate_thumbnail",
    "get_image",
    "queue_prompt",
]


class _ForwardingShim(types.ModuleType):
    """Forwards attribute writes to ``slop_studio.backends.local``.

    Tests monkeypatch module-level names on this shim (e.g.
    ``slop_studio.comfyui.OUTPUT_DIR``, ``slop_studio.comfyui.generate_thumbnail``)
    and then invoke code whose function bodies live in ``backends.local``.
    Those bodies resolve free names against ``backends.local``'s namespace, not
    the shim's — so writes to the shim also need to land on ``backends.local``
    for the patch to take effect.

    The target module is captured once at import time (``_local``) rather than
    re-imported inside ``__setattr__``, because re-entering the import
    machinery from inside a setattr called during this shim's own reload
    interacts badly with submodule registration (it swaps the ``local`` entry
    on the ``slop_studio.backends`` package to point at this shim).

    Forwarding is guarded so we only mirror names that already exist on the
    real module; new attributes set on the shim are not leaked, and the
    ``_local`` back-reference itself is not forwarded.
    """

    def __setattr__(self, name: str, value: object) -> None:
        super().__setattr__(name, value)
        # Skip dunders (reload rewrites __name__/__file__/__spec__/__class__
        # on the shim; forwarding them would overwrite backends.local's own
        # identity) and the shim's private back-reference.
        if name.startswith("__") or name == "_local":
            return
        if hasattr(_local, name):
            setattr(_local, name, value)


sys.modules[__name__].__class__ = _ForwardingShim
