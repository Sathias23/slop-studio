## Deferred from: code review of 3-2-delete-workflow-templates (2026-03-30)

- **TOCTOU race between `is_file()` and `meta_path.unlink()`** (`slop_studio/templates.py`) — Concurrent delete between check and unlink raises `FileNotFoundError` (caught as OSError), returns misleading retryable `storage_error`. Systemic pattern across module; low risk for single-user tool.
- **Null bytes in template name bypass validation and raise `ValueError`** (`slop_studio/templates.py`) — A name like `"foo\x00bar"` passes `_validate_template_name` and causes `ValueError` on `Path.unlink()`, which is not caught by `except OSError`. Pre-existing gap in validator.
- **`_validate_template_name` does not reject backslash** (`slop_studio/templates.py`) — Pre-existing gap already noted in 3-1 deferred items. POSIX-safe; no path traversal risk on Linux/macOS.
- **`TEMPLATES_DIR` misconfiguration (path is a file, not a directory) produces opaque error message** (`slop_studio/templates.py`) — `meta_path.is_file()` returns `False`, subsequent `unlink()` raises `NotADirectoryError` (caught as OSError) with unhelpful "Failed to delete template" message. Pre-existing pattern.
- **No resolved-path confinement check after `_validate_template_name`** (`slop_studio/templates.py`) — Character blacklist in validator is defense-in-depth; no `resolve().is_relative_to()` guard ensures final paths stay within `TEMPLATES_DIR`. Pre-existing pattern across module.

## Deferred from: code review of 3-1-add-and-update-workflow-templates-with-validation (2026-03-30)

- **`_validate_metadata` "name" check is dead code** (`slop_studio/templates.py:27-30`) — `add_template` and `update_template` always inject `metadata["name"] = name` before calling the validator, so the "name" required field check never fires.
- **`_validate_template_name` does not block backslash or null bytes** (`slop_studio/templates.py:13-22`) — Outside FR8 scope (spec only requires rejecting `/`, `..`, leading `.`); low risk on POSIX, moot for null bytes in practice.
- **`update_template` silently creates `.json` for broken template** (`slop_studio/templates.py:162-165`) — If `.meta.json` exists but `.json` is missing, providing `workflow_json` creates the file rather than updating. Spec does not address this broken-template recovery scenario.
- **`test_add_template_storage_error` global `Path.write_text` monkeypatch is fragile** (`tests/test_templates.py:259`) — Monkeypatches `Path.write_text` globally; could interfere with fixture teardown or future tests writing non-meta `.json` files.
- **No positive integer validation for `aspect_ratios` width/height** (`slop_studio/templates.py:52-53`) — Spec requires `int` type only; zero or negative dimensions pass validation and would fail at generation time.

## Deferred from: review of spec-defensive-hardening (2026-03-30)

- **TOCTOU race in filename collision check** (`slop_studio/comfyui.py:344-353`) — `os.path.exists()` followed by `open("wb")` is non-atomic. Concurrent `get_image()` calls for the same filename could overwrite each other. Low risk for single-user local tool; consider `O_CREAT|O_EXCL` or tempfile-based approach if concurrency becomes relevant.

## Deferred from: code review of 2-2-job-submission-with-input-injection-and-seed-randomization (2026-03-29)

- **`_inject_inputs` KeyError when node_id absent from workflow** (`slop_studio/comfyui.py:22`) — Meta file references a node_id not present in the workflow JSON. Phase 2 cross-reference validation per spec anti-pattern rule ("Do NOT validate workflow JSON structure beyond 'is it a dict'").
- **`_inject_resolution` KeyError when resolution node_id absent from workflow** (`slop_studio/comfyui.py:43`) — Same pattern as above for resolution nodes. Phase 2 cross-reference validation.
- **Template name path traversal in comfyui.py** (`slop_studio/comfyui.py:51-52`) — `template_name` used directly in path construction with no containment check. Story 3.1 scope per spec anti-pattern rule (matches deferred item from Story 2.1).
- **`_inject_inputs` KeyError if workflow node lacks `"inputs"` sub-key** (`slop_studio/comfyui.py:23`) — Direct `workflow[node_id]["inputs"]` access without guard. Workflow structure validation is Phase 2.
- **`PermissionError` from `is_file()` not caught** (`slop_studio/comfyui.py:54`) — `Path.is_file()` can raise `PermissionError` on restricted filesystems; not wrapped in try/except. Unusual edge case, low risk for single-user local tool.

## Deferred from: code review of 2-1-template-discovery-and-starter-templates (2026-03-29)

- **Path traversal in `get_template`** (`slop_studio/templates.py:35`) — `template_name` is used directly in path construction with no containment check; a `"../../"` sequence could read arbitrary `.meta.json`-named files outside `TEMPLATES_DIR`. Story 3.1 scope (template management/write operations).
- **`seed: 0` hardcoded in workflow JSON** (`templates/flux2_klein.json`, `flux2_klein_ultrawide.json`) — Static seed produces identical outputs if not overridden by caller. Story 2.2 (job-submission-with-input-injection-and-seed-randomization) will inject a random seed.
- **Symlinks in templates directory followed without guard** (`slop_studio/templates.py:18`) — `glob("*.meta.json")` follows symlinks; a malicious symlink could expose files outside the templates directory. Story 3.1 scope.
- **`TEMPLATES_DIR` default is a relative path** (`slop_studio/config.py`) — `"./templates"` is CWD-dependent. Explicitly accepted for MVP; server expected to run from project root. See also 1-1 deferred item.
- **Test module reload side effects in `test_mcp_tools_registered`** (`tests/test_templates.py:120`) — `importlib.reload(slop_studio.server)` may double-register MCP tools and resets `TEMPLATES_DIR` to the real path after the test. Passes in practice with current test suite; revisit if tool registration becomes additive.

## Deferred from: code review of 2-3 and 2-4 (2026-03-30)

- **Uncaught httpx.RequestError subclasses in check_job/get_image** (`slop_studio/comfyui.py:207-231`) — Only `ConnectError` and `TimeoutException` are caught; `ReadError`, `RemoteProtocolError`, etc. propagate unhandled. Consistent with existing codebase pattern from prior stories.
- **Malformed ComfyUI responses crash with AttributeError** (`slop_studio/comfyui.py:160-161, 270-271`) — `_fetch_job_status` and `get_image` assume `data[prompt_id]` and output nodes are dicts. Non-dict values cause unhandled AttributeError. No response structure validation per architecture decision.
- **File overwrite on filename collision** (`slop_studio/comfyui.py:315-318`) — Same ComfyUI filename (e.g., `ComfyUI_00042_.png`) across different prompt_ids silently overwrites previous images. No uniqueness/counter mechanism. Not in scope for MVP.
- **Image response size not validated** (`slop_studio/comfyui.py:303`) — `response.content` reads entire response into memory with no size limit. Extremely large responses could cause OOM. Phase 2 hardening.

## Deferred from: code review of 1-2-fastmcp-server-with-stdio-transport-and-startup-validation (2026-03-29)

- **Invalid/empty COMFYUI_URL causes unhandled exception** (`slop_studio/server.py:18`) — `httpx.InvalidURL` or `httpx.UnsupportedProtocol` propagates without logging when URL is empty or malformed. Pre-existing config design; previously deferred in Story 1.1. Consider startup URL validation in a future hardening story.
- **Uncaught httpx.RemoteProtocolError and other HTTPError subclasses** (`slop_studio/server.py:19-32`) — Only `ConnectError`, `TimeoutException`, `HTTPStatusError` are caught and logged. Other `httpx.HTTPError` subclasses (e.g., `RemoteProtocolError`) propagate without a diagnostic log entry. Out of scope per spec requirements.
- **Trailing slash in COMFYUI_URL produces double-slash path** (`slop_studio/server.py:18`) — If `COMFYUI_URL` ends with `/`, health check hits `//system_stats`. Most servers tolerate this but ComfyUI may return 404. URL normalization not required by spec.
- **TimeoutException log message is generic** (`slop_studio/server.py:23-25`) — "ComfyUI connection timed out" is logged for all timeout subtypes (read, pool, connect). Low severity cosmetic issue.

## Deferred from: code review of 1-1-package-structure-configuration-and-error-types (2026-03-29)

- **Config constants evaluated at import time** (`slop_studio/config.py:3-5`) — Architectural design decision per spec (module-level constants pattern). The `importlib.reload` workaround in tests is the intended approach. Revisit if config ever needs dynamic reloading at runtime.
- **Relative path defaults are cwd-dependent** (`slop_studio/config.py:4-5`) — `./templates` and `./output` are spec-mandated defaults (FR26-28). Consider resolving to absolute paths in a future story if server launch location becomes an issue.
- **error_type and message accept empty strings / no runtime validation** (`slop_studio/errors.py:12-19`) — Spec explicitly prohibits runtime validation; taxonomy enforced by convention. Consider adding an enum or Literal type in a later story if type-safety is desired.
- **fastmcp pinned with floor only (`>=3.1.1`)** (`pyproject.toml:8`) — Dev notes explicitly say not to pin; uv.lock provides reproducibility. Revisit upper bound before any major fastmcp version drops.
- **Empty env var (`""`) bypasses defaults** (`slop_studio/config.py:3-5`) — Spec anti-pattern rules prohibit validation at this layer. Consider a startup check in Story 1.2 when the server validates its environment.
