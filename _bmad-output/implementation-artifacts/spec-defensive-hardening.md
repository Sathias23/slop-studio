---
title: 'Defensive hardening: config validation, error messages, and filename collision'
type: 'bugfix'
created: '2026-03-30'
status: 'done'
baseline_commit: '8fe95bd'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Several real-world usage risks exist: (1) image files silently overwrite on filename collision, (2) config values (COMFYUI_URL, TEMPLATES_DIR, OUTPUT_DIR) break silently when empty or malformed, (3) template injection crashes with unhelpful KeyErrors when meta/workflow drift.

**Approach:** Add a config helper that treats empty env vars as unset, normalize URL trailing slashes, resolve TEMPLATES_DIR to an absolute path relative to the package, add collision-safe file naming, and wrap template injection with actionable error logging instead of raw KeyErrors.

## Boundaries & Constraints

**Always:** Log actionable messages on every guarded path. Preserve existing behavior for valid inputs — these are guardrails, not behavior changes. Keep all fixes in existing files.

**Ask First:** Any change to the MCP tool signatures or return types.

**Never:** Add workflow JSON structure validation beyond what's needed for the specific KeyError guards. Do not change template file contents. Do not add retry logic.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Empty COMFYUI_URL env var | `COMFYUI_URL=""` | Falls back to `http://localhost:8188` | N/A |
| Trailing slash URL | `COMFYUI_URL="http://host:8188/"` | Stripped to `http://host:8188` | N/A |
| Non-http URL | `COMFYUI_URL="ftp://bad"` | Startup raises `ValueError` with clear message | ValueError at config load |
| Filename collision | Two images with same name in same date dir | Second file saved as `name_001.png`, `_002`, etc. | N/A |
| Meta references missing node_id | `node_id: "99"` not in workflow dict | Logs error, skips that input, continues | Logged warning, no crash |
| Invalid aspect_ratio key | `aspect_ratio="5:3"` not in meta | Logs error, returns without modifying workflow | Logged warning, no crash |
| Node exists but lacks "inputs" | Workflow node is `{"class_type": "X"}` | Logs error, skips that input | Logged warning, no crash |
| Resolution node_id missing from workflow | `res_node["node_id"]` not in workflow | Logs error, skips that node | Logged warning, no crash |
| TEMPLATES_DIR launched from wrong CWD | Server started from `/tmp` | Templates still found via package-relative path | N/A |

</frozen-after-approval>

## Code Map

- `comfyclaude/config.py` -- Config constants, env var loading, defaults
- `comfyclaude/comfyui.py` -- Template injection (`_inject_inputs`, `_inject_resolution`) and image saving
- `comfyclaude/server.py` -- Health check URL construction
- `tests/test_config.py` -- New: config hardening tests
- `tests/test_comfyui.py` -- Existing tests, add injection edge-case tests

## Tasks & Acceptance

**Execution:**
- [x] `comfyclaude/config.py` -- Add `_env_or_default()` helper that returns default when env var is empty string. Apply to all three config vars. Normalize COMFYUI_URL (strip trailing slash, validate http(s) scheme). Resolve TEMPLATES_DIR default to absolute path relative to package.
- [x] `comfyclaude/comfyui.py` -- Guard `_inject_inputs`: check node_id exists in workflow, check node has "inputs" key, log and skip on miss. Guard `_inject_resolution`: check aspect_ratio key exists, check each resolution node_id exists, log and skip on miss.
- [x] `comfyclaude/comfyui.py` -- Add collision-safe filename in image saving: when target path exists, append `_001`, `_002` suffix before extension.
- [x] `tests/test_config.py` -- Test empty env var fallback, trailing slash stripping, invalid URL rejection, absolute path resolution for TEMPLATES_DIR.
- [x] `tests/test_comfyui.py` -- Test injection with missing node_id, missing "inputs" key, invalid aspect_ratio. Test filename collision produces suffixed file.

**Acceptance Criteria:**
- Given `COMFYUI_URL=""` in env, when config loads, then `COMFYUI_URL == "http://localhost:8188"`
- Given `COMFYUI_URL="http://host:8188/"`, when config loads, then no trailing slash
- Given a workflow missing a referenced node_id, when `_inject_inputs` runs, then no exception raised and error is logged
- Given two images with identical filenames, when saved to same date dir, then both files exist with distinct names
- Given server launched from `/tmp` with no env override, when templates are listed, then templates are found

## Verification

**Commands:**
- `uv run pytest tests/ -v` -- expected: all tests pass including new edge-case tests
- `uv run pytest tests/ --tb=short -q` -- expected: 0 failures

## Suggested Review Order

**Config hardening**

- `_env_or_default` helper: treats empty env vars as unset, all three config vars use it
  [`config.py:6`](../../comfyclaude/config.py#L6)

- URL normalization: trailing slash strip + http(s) scheme validation at import time
  [`config.py:13`](../../comfyclaude/config.py#L13)

- TEMPLATES_DIR default resolved to absolute path relative to package directory
  [`config.py:20`](../../comfyclaude/config.py#L20)

**Injection guards**

- `_inject_inputs` now checks node_id exists, node has "inputs", logs and skips on miss
  [`comfyui.py:27`](../../comfyclaude/comfyui.py#L27)

- `_inject_resolution` validates aspect_ratio key, resolution node presence, and "inputs" key
  [`comfyui.py:52`](../../comfyclaude/comfyui.py#L52)

**Filename collision avoidance**

- When output path exists, appends `_001`..`_999` suffix before extension
  [`comfyui.py:344`](../../comfyclaude/comfyui.py#L344)

**Tests**

- Config hardening: empty fallback, trailing slash, invalid scheme, absolute path
  [`test_config.py:40`](../../tests/test_config.py#L40)

- Injection guards: missing node, missing inputs key, incomplete definition, invalid aspect ratio
  [`test_comfyui.py:693`](../../tests/test_comfyui.py#L693)

- Filename collision: pre-creates colliding file, verifies suffixed output
  [`test_comfyui.py:767`](../../tests/test_comfyui.py#L767)
