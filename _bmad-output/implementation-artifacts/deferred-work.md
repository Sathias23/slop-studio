## Deferred from: code review of 2-2-job-submission-with-input-injection-and-seed-randomization (2026-03-29)

- **`_inject_inputs` KeyError when node_id absent from workflow** (`comfyclaude/comfyui.py:22`) — Meta file references a node_id not present in the workflow JSON. Phase 2 cross-reference validation per spec anti-pattern rule ("Do NOT validate workflow JSON structure beyond 'is it a dict'").
- **`_inject_resolution` KeyError when resolution node_id absent from workflow** (`comfyclaude/comfyui.py:43`) — Same pattern as above for resolution nodes. Phase 2 cross-reference validation.
- **Template name path traversal in comfyui.py** (`comfyclaude/comfyui.py:51-52`) — `template_name` used directly in path construction with no containment check. Story 3.1 scope per spec anti-pattern rule (matches deferred item from Story 2.1).
- **`_inject_inputs` KeyError if workflow node lacks `"inputs"` sub-key** (`comfyclaude/comfyui.py:23`) — Direct `workflow[node_id]["inputs"]` access without guard. Workflow structure validation is Phase 2.
- **`PermissionError` from `is_file()` not caught** (`comfyclaude/comfyui.py:54`) — `Path.is_file()` can raise `PermissionError` on restricted filesystems; not wrapped in try/except. Unusual edge case, low risk for single-user local tool.

## Deferred from: code review of 2-1-template-discovery-and-starter-templates (2026-03-29)

- **Path traversal in `get_template`** (`comfyclaude/templates.py:35`) — `template_name` is used directly in path construction with no containment check; a `"../../"` sequence could read arbitrary `.meta.json`-named files outside `TEMPLATES_DIR`. Story 3.1 scope (template management/write operations).
- **`seed: 0` hardcoded in workflow JSON** (`templates/flux2_klein.json`, `flux2_klein_ultrawide.json`) — Static seed produces identical outputs if not overridden by caller. Story 2.2 (job-submission-with-input-injection-and-seed-randomization) will inject a random seed.
- **Symlinks in templates directory followed without guard** (`comfyclaude/templates.py:18`) — `glob("*.meta.json")` follows symlinks; a malicious symlink could expose files outside the templates directory. Story 3.1 scope.
- **`TEMPLATES_DIR` default is a relative path** (`comfyclaude/config.py`) — `"./templates"` is CWD-dependent. Explicitly accepted for MVP; server expected to run from project root. See also 1-1 deferred item.
- **Test module reload side effects in `test_mcp_tools_registered`** (`tests/test_templates.py:120`) — `importlib.reload(comfyclaude.server)` may double-register MCP tools and resets `TEMPLATES_DIR` to the real path after the test. Passes in practice with current test suite; revisit if tool registration becomes additive.

## Deferred from: code review of 1-2-fastmcp-server-with-stdio-transport-and-startup-validation (2026-03-29)

- **Invalid/empty COMFYUI_URL causes unhandled exception** (`comfyclaude/server.py:18`) — `httpx.InvalidURL` or `httpx.UnsupportedProtocol` propagates without logging when URL is empty or malformed. Pre-existing config design; previously deferred in Story 1.1. Consider startup URL validation in a future hardening story.
- **Uncaught httpx.RemoteProtocolError and other HTTPError subclasses** (`comfyclaude/server.py:19-32`) — Only `ConnectError`, `TimeoutException`, `HTTPStatusError` are caught and logged. Other `httpx.HTTPError` subclasses (e.g., `RemoteProtocolError`) propagate without a diagnostic log entry. Out of scope per spec requirements.
- **Trailing slash in COMFYUI_URL produces double-slash path** (`comfyclaude/server.py:18`) — If `COMFYUI_URL` ends with `/`, health check hits `//system_stats`. Most servers tolerate this but ComfyUI may return 404. URL normalization not required by spec.
- **TimeoutException log message is generic** (`comfyclaude/server.py:23-25`) — "ComfyUI connection timed out" is logged for all timeout subtypes (read, pool, connect). Low severity cosmetic issue.

## Deferred from: code review of 1-1-package-structure-configuration-and-error-types (2026-03-29)

- **Config constants evaluated at import time** (`comfyclaude/config.py:3-5`) — Architectural design decision per spec (module-level constants pattern). The `importlib.reload` workaround in tests is the intended approach. Revisit if config ever needs dynamic reloading at runtime.
- **Relative path defaults are cwd-dependent** (`comfyclaude/config.py:4-5`) — `./templates` and `./output` are spec-mandated defaults (FR26-28). Consider resolving to absolute paths in a future story if server launch location becomes an issue.
- **error_type and message accept empty strings / no runtime validation** (`comfyclaude/errors.py:12-19`) — Spec explicitly prohibits runtime validation; taxonomy enforced by convention. Consider adding an enum or Literal type in a later story if type-safety is desired.
- **fastmcp pinned with floor only (`>=3.1.1`)** (`pyproject.toml:8`) — Dev notes explicitly say not to pin; uv.lock provides reproducibility. Revisit upper bound before any major fastmcp version drops.
- **Empty env var (`""`) bypasses defaults** (`comfyclaude/config.py:3-5`) — Spec anti-pattern rules prohibit validation at this layer. Consider a startup check in Story 1.2 when the server validates its environment.
