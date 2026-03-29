# Story 2.2: Job Submission with Input Injection & Seed Randomization

Status: done

## Story

As a developer using Claude Code,
I want to submit an image generation job by specifying a template and my inputs,
So that ComfyUI generates an image based on my request without me touching the workflow JSON.

## Acceptance Criteria

1. **Given** a valid template name and input values (e.g., prompt text)
   **When** I call the `queue_prompt` MCP tool with `template_name`, `inputs`, and optional `aspect_ratio`
   **Then** the server loads the template, injects inputs, and submits to ComfyUI's `/prompt` API, returning a `prompt_id` (FR9, FR13)

2. **Given** the template defines input nodes (e.g., node "6" is a text prompt)
   **When** the server prepares the workflow
   **Then** user-provided input values are injected into the correct workflow nodes based on template input definitions (FR10)

3. **Given** the workflow contains seed or noise_seed fields across multiple nodes
   **When** the server prepares the workflow for submission
   **Then** all seed fields are randomized to prevent ComfyUI cache hits (FR11)

4. **Given** the user specifies `aspect_ratio: "16:9"` and the template defines resolution nodes and aspect ratio mappings
   **When** the server prepares the workflow
   **Then** it maps the aspect ratio label to width/height dimensions and injects them into the template-defined resolution nodes (FR12)

5. **Given** no aspect ratio is specified
   **When** the server prepares the workflow
   **Then** it uses the template's default resolution

6. **Given** ComfyUI is unreachable when submitting
   **When** the `queue_prompt` tool is called
   **Then** it returns a transient error with `error_type: "unreachable"` and `retry_suggested: true`

7. **Given** the template name doesn't exist
   **When** `queue_prompt` is called
   **Then** it returns a terminal error with `error_type: "invalid_inputs"`

8. **Given** the story is complete
   **When** I run `uv run pytest tests/test_comfyui.py`
   **Then** all job submission tests pass with mocked HTTP via respx

## Tasks / Subtasks

- [x] Task 1: Create `comfyclaude/comfyui.py` with workflow preparation and submission functions (AC: #1, #2, #3, #4, #5, #6)
  - [x] 1.1 Create `comfyclaude/comfyui.py` with `async def queue_prompt(template_name: str, inputs: dict, aspect_ratio: str | None = None) -> dict`
  - [x] 1.2 Load template workflow JSON from `{TEMPLATES_DIR}/{template_name}.json` and metadata from `{template_name}.meta.json`
  - [x] 1.3 Deep copy the workflow dict before mutation
  - [x] 1.4 Implement `_inject_inputs(workflow, meta_inputs, user_inputs)` — for each key in `user_inputs`, look up the input definition in `meta["inputs"]`, set `workflow[node_id]["inputs"][field] = value`
  - [x] 1.5 Implement `_randomize_seeds(workflow)` — walk all nodes, find any `"seed"` or `"noise_seed"` field with an integer value, replace with `random.randint(0, 2**63 - 1)`
  - [x] 1.6 Implement `_inject_resolution(workflow, meta, aspect_ratio)` — lookup `aspect_ratio` in `meta["aspect_ratios"]`, get width/height, inject into each node in `meta["resolution_nodes"]`
  - [x] 1.7 Submit prepared workflow to ComfyUI `POST /prompt` with `{"prompt": workflow}` body, return `{"status": "success", "prompt_id": response_json["prompt_id"]}`
  - [x] 1.8 Handle `httpx.ConnectError` / `httpx.TimeoutException` → `transient_error("unreachable", ...)`
  - [x] 1.9 Handle missing template → `terminal_error("invalid_inputs", ...)`
  - [x] 1.10 Handle invalid aspect ratio (not in template's supported list) → `terminal_error("invalid_inputs", ...)`
  - [x] 1.11 Handle missing required inputs → `terminal_error("invalid_inputs", ...)`
- [x] Task 2: Register `queue_prompt` MCP tool in `server.py` (AC: #1)
  - [x] 2.1 Add `@mcp.tool()` for `queue_prompt` with rich docstring
  - [x] 2.2 Tool function delegates to `comfyui.queue_prompt()`
- [x] Task 3: Write tests in `tests/test_comfyui.py` (AC: #8)
  - [x] 3.1 Test successful job submission returns prompt_id
  - [x] 3.2 Test input injection places values in correct workflow nodes
  - [x] 3.3 Test seed randomization modifies all seed/noise_seed fields
  - [x] 3.4 Test aspect ratio injection sets correct width/height on resolution nodes
  - [x] 3.5 Test default resolution preserved when no aspect ratio specified
  - [x] 3.6 Test unreachable ComfyUI returns transient error
  - [x] 3.7 Test missing template returns terminal error
  - [x] 3.8 Test invalid aspect ratio returns terminal error
  - [x] 3.9 Test missing required input returns terminal error

### Review Findings

- [x] [Review][Patch] 5xx HTTPStatusError should return transient_error, not terminal_error — Decision: split by status code: 4xx → terminal_error("invalid_workflow"), 5xx → transient_error("unreachable", retry_suggested: true). [comfyclaude/comfyui.py:102-107]
- [x] [Review][Patch] Uncaught httpx.RequestError subclasses propagate unhandled — `ConnectError` and `TimeoutException` are caught, but `ReadError`, `RemoteProtocolError`, `TooManyRedirects`, etc. escape as unhandled exceptions instead of returning `transient_error`. Fixed: replaced `(httpx.ConnectError, httpx.TimeoutException)` with `httpx.RequestError`. [comfyclaude/comfyui.py:98-101]
- [x] [Review][Patch] KeyError crash on missing prompt_id in 200 response — `data["prompt_id"]` has no guard; a malformed success body crashes instead of returning a structured error. Fixed: use `data.get("prompt_id")` with nil check. [comfyclaude/comfyui.py:110]
- [x] [Review][Patch] response.json() JSONDecodeError outside try/except — a non-JSON 2xx body from ComfyUI propagates uncaught. Fixed: wrapped in try/except. [comfyclaude/comfyui.py:109]
- [x] [Review][Patch] conftest.py reload_config doesn't reload comfyclaude.comfyui — stale COMFYUI_URL/TEMPLATES_DIR may bleed into subsequent tests that don't use the templates_dir fixture. Fixed: added `importlib.reload(comfyclaude.comfyui)` to autouse fixture. [tests/conftest.py:11]
- [x] [Review][Patch] No isinstance(workflow, dict) guard after JSON parse — a valid JSON array or string in a template file crashes downstream in _inject_inputs/_randomize_seeds instead of returning a clean terminal_error. Fixed: added guard. [comfyclaude/comfyui.py:60]
- [x] [Review][Patch] test_seed_randomization: assert seed != 0 is redundant when mock pins seed to 42. Fixed: removed redundant assertion. [tests/test_comfyui.py:124]
- [x] [Review][Defer] _inject_inputs KeyError when node_id absent from workflow — Phase 2 cross-reference validation per spec anti-pattern rule. [comfyclaude/comfyui.py:22] — deferred, pre-existing
- [x] [Review][Defer] _inject_resolution KeyError when resolution node_id absent from workflow — Phase 2 cross-reference validation. [comfyclaude/comfyui.py:43] — deferred, pre-existing
- [x] [Review][Defer] Template name path traversal via Path construction — Story 3.1 scope per spec anti-pattern rule. [comfyclaude/comfyui.py:51-52] — deferred, pre-existing
- [x] [Review][Defer] _inject_inputs KeyError if workflow node lacks "inputs" sub-key — workflow structure validation is Phase 2. [comfyclaude/comfyui.py:23] — deferred, pre-existing
- [x] [Review][Defer] PermissionError from is_file() not caught — unusual OS edge case, low risk for single-user local tool. [comfyclaude/comfyui.py:54] — deferred, pre-existing

## Dev Notes

### Architecture Compliance

**Module boundaries** — Per architecture, `comfyui.py` is the **only** module that makes HTTP calls to ComfyUI. It encapsulates `httpx.AsyncClient` usage and translates ComfyUI API responses into internal dicts. `server.py` registers the MCP tool and delegates to `comfyui.py`. `templates.py` is NOT modified — `comfyui.py` reads template files directly (both `.json` and `.meta.json`) since it needs the workflow JSON for preparation, not just the metadata summary.

**Tool return format** — All MCP tools return dicts with a `status` field:
- Success: `{"status": "success", "prompt_id": "..."}`
- Error: Use `terminal_error()` / `transient_error()` from `comfyclaude.errors`

**Tool registration** — `server.py` is the only module that imports FastMCP and registers tools via `@mcp.tool()`. The `queue_prompt` tool function in `server.py` is a thin wrapper delegating to `comfyui.py`.

**httpx usage** — Create a new `httpx.AsyncClient` per request with `timeout=30.0` (NFR1). Do NOT create a persistent client — that is Phase 2 (persistent httpx.AsyncClient via FastMCP lifespan). The lifespan in `server.py` already has a client for the health check; `comfyui.py` creates its own for runtime calls.

### Technical Requirements

**ComfyUI `/prompt` API:**
- Endpoint: `POST {COMFYUI_URL}/prompt`
- Request body: `{"prompt": <workflow_dict>}`
- Success response (200): `{"prompt_id": "<uuid>", "number": <int>, "node_errors": {}}`
- Error response: Various — ComfyUI returns 400 with error details for invalid workflows

**Input injection algorithm:**
```python
import copy
import json
import random
import logging
from pathlib import Path

import httpx

from comfyclaude.config import COMFYUI_URL, TEMPLATES_DIR
from comfyclaude.errors import transient_error, terminal_error

logger = logging.getLogger(__name__)

def _inject_inputs(workflow: dict, meta_inputs: dict, user_inputs: dict) -> None:
    """Inject user-provided input values into the workflow nodes in-place."""
    for input_name, value in user_inputs.items():
        if input_name not in meta_inputs:
            continue  # Ignore extra inputs silently
        input_def = meta_inputs[input_name]
        node_id = input_def["node_id"]
        field = input_def["field"]
        workflow[node_id]["inputs"][field] = value
```

**Seed randomization algorithm:**
```python
def _randomize_seeds(workflow: dict) -> None:
    """Replace all seed/noise_seed fields with random values to prevent cache hits."""
    for node in workflow.values():
        inputs = node.get("inputs", {})
        for key in ("seed", "noise_seed"):
            if key in inputs and isinstance(inputs[key], int):
                inputs[key] = random.randint(0, 2**63 - 1)
```

Walk every node in the workflow dict. Check `inputs` for `seed` or `noise_seed` keys. Only replace if the value is an integer (node references are `[node_id, output_index]` lists — skip those). The current `flux2_klein.json` has `seed: 0` in node 3 (KSampler).

**Aspect ratio injection:**
```python
def _inject_resolution(workflow: dict, meta: dict, aspect_ratio: str | None) -> None:
    """Map aspect ratio label to dimensions and inject into resolution nodes."""
    if aspect_ratio is None:
        return  # Keep template defaults

    ratios = meta.get("aspect_ratios", {})
    if aspect_ratio not in ratios:
        return  # Caller should validate before calling

    dims = ratios[aspect_ratio]
    for res_node in meta.get("resolution_nodes", []):
        node_id = res_node["node_id"]
        workflow[node_id]["inputs"][res_node["width_field"]] = dims["width"]
        workflow[node_id]["inputs"][res_node["height_field"]] = dims["height"]
```

For `flux2_klein` with `aspect_ratio="16:9"`: looks up `{"width": 1344, "height": 768}`, sets node 5's `width=1344`, `height=768`.

**Required inputs validation:**
```python
# Check required inputs are provided
for input_name, input_def in meta_inputs.items():
    if input_def.get("type") == "required" and input_name not in user_inputs:
        return terminal_error("invalid_inputs",
            f"Missing required input '{input_name}': {input_def.get('description', '')}")
```

**Full `queue_prompt` function outline:**
```python
async def queue_prompt(template_name: str, inputs: dict, aspect_ratio: str | None = None) -> dict:
    # 1. Load template files
    workflow_path = Path(TEMPLATES_DIR) / f"{template_name}.json"
    meta_path = Path(TEMPLATES_DIR) / f"{template_name}.meta.json"

    if not workflow_path.is_file() or not meta_path.is_file():
        return terminal_error("invalid_inputs", f"Template '{template_name}' not found")

    try:
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return terminal_error("invalid_inputs", f"Failed to read template '{template_name}': {exc}")

    # 2. Validate required inputs
    meta_inputs = meta.get("inputs", {})
    for input_name, input_def in meta_inputs.items():
        if input_def.get("type") == "required" and input_name not in inputs:
            return terminal_error("invalid_inputs",
                f"Missing required input '{input_name}': {input_def.get('description', '')}")

    # 3. Validate aspect ratio
    if aspect_ratio is not None and aspect_ratio not in meta.get("aspect_ratios", {}):
        supported = list(meta.get("aspect_ratios", {}).keys())
        return terminal_error("invalid_inputs",
            f"Unsupported aspect ratio '{aspect_ratio}'. Supported: {supported}")

    # 4. Prepare workflow (deep copy to avoid mutating cached data)
    prepared = copy.deepcopy(workflow)
    _inject_inputs(prepared, meta_inputs, inputs)
    _randomize_seeds(prepared)
    _inject_resolution(prepared, meta, aspect_ratio)

    # 5. Submit to ComfyUI
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{COMFYUI_URL}/prompt",
                json={"prompt": prepared},
            )
            response.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException):
        return transient_error("unreachable",
            f"Cannot connect to ComfyUI at {COMFYUI_URL}")
    except httpx.HTTPStatusError as exc:
        # ComfyUI returns 400 for invalid workflows
        error_body = exc.response.text
        return terminal_error("invalid_workflow",
            f"ComfyUI rejected the workflow: {error_body[:500]}")

    data = response.json()
    return {"status": "success", "prompt_id": data["prompt_id"]}
```

### MCP Tool Registration

**`server.py` addition:**
```python
from comfyclaude import comfyui

@mcp.tool()
async def queue_prompt(template_name: str, inputs: dict, aspect_ratio: str | None = None) -> dict:
    """Submit an image generation job using a workflow template.

    Loads the named template, injects your input values into the correct
    workflow nodes, randomizes all seeds to avoid cached results, and submits
    the workflow to ComfyUI. Returns a prompt_id to track the job.

    Call list_templates first to see available templates, then get_template
    to check required inputs. The inputs dict keys must match the template's
    input names (e.g., {"prompt": "a sunset over mountains"}).

    Optional aspect_ratio overrides the default resolution (e.g., "16:9",
    "9:16", "1:1"). Use get_template to see supported aspect ratios.
    """
    return await comfyui.queue_prompt(template_name, inputs, aspect_ratio)
```

### Previous Story Intelligence

**Story 2.1 established:**
- `comfyclaude/templates.py` with `list_templates()` and `get_template()` — read-only functions for template metadata. Do NOT import from or modify `templates.py` for job submission.
- `templates/` directory with starter templates: `flux2_klein.json`, `flux2_klein.meta.json`, `flux2_klein_ultrawide.json`, `flux2_klein_ultrawide.meta.json`
- Template meta schema: `inputs` dict maps input names to `{node_id, field, type, description}`, `aspect_ratios` maps labels to `{width, height}`, `resolution_nodes` is a list of `{node_id, width_field, height_field}`
- Review findings: `get_template` now has JSON error handling, status key ordering fixed, OSError caught, explicit UTF-8 encoding used

**Story 1.2 established:**
- `comfyclaude/server.py` with `mcp = FastMCP("comfyclaude", lifespan=lifespan)` — add `@mcp.tool()` for `queue_prompt` after existing tool registrations
- Lifespan creates a temporary `httpx.AsyncClient` — do NOT reuse it for runtime calls
- respx test patterns for mocking httpx

**Story 1.1 established:**
- `comfyclaude/config.py` with `COMFYUI_URL`, `TEMPLATES_DIR`, `OUTPUT_DIR` constants
- `comfyclaude/errors.py` with `transient_error()` / `terminal_error()`
- `tests/conftest.py` with `autouse` fixture reloading `comfyclaude.config` after each test

**Epic 1 retrospective takeaways:**
- Detailed story specs enable single-pass implementation — maintain this level of detail
- Code review catches real issues — deferred `seed: 0` from Story 2.1 is now addressed in THIS story
- Import-time config constants require reload pattern in tests — use same `monkeypatch + importlib.reload` approach

**Deferred work resolved by this story:**
- `seed: 0` hardcoded in workflow JSON (`templates/flux2_klein.json`) — `_randomize_seeds()` replaces all seed fields before submission, so the hardcoded 0 is never sent to ComfyUI

### Library & Framework Requirements

| Package | Version (installed) | Usage in this story |
|---------|-------------------|---------------------|
| fastmcp | 3.1.1 | `@mcp.tool()` decorator for queue_prompt registration |
| httpx | 0.28.1 | `AsyncClient` for `POST /prompt` to ComfyUI |
| respx | 0.22.0 | Mock httpx requests in tests |
| pytest | 9.0.2 | Test framework |

**httpx.AsyncClient usage (v0.28.1):**
- `async with httpx.AsyncClient(timeout=30.0) as client:` — per-request client
- `await client.post(url, json=body)` — POST with JSON body
- `response.raise_for_status()` — raises `HTTPStatusError` for 4xx/5xx
- `response.json()` — parse response body

**No new dependencies needed.** All libraries are already installed from prior stories.

### File Structure Requirements

**Create:**
```
comfyclaude/comfyui.py     # ComfyUI HTTP client: queue_prompt, workflow preparation helpers
tests/test_comfyui.py      # Job submission tests with respx-mocked HTTP
```

**Modify:**
```
comfyclaude/server.py      # Add @mcp.tool() for queue_prompt
```

**Do NOT create or modify:**
- `comfyclaude/templates.py` — Template reading in `comfyui.py` is independent (reads JSON files directly)
- `comfyclaude/config.py` — No changes needed
- `comfyclaude/errors.py` — No changes needed
- `comfyclaude/init.py` — Story 4.1
- `templates/` — Starter templates already exist and are correct
- `main.py` — No changes needed

### Testing Requirements

Tests in `tests/test_comfyui.py` using both filesystem fixtures (template files) and respx (HTTP mocking):

**Fixture pattern:**
```python
import json
import importlib
import pytest
import respx
import httpx

import comfyclaude.config
import comfyclaude.comfyui

@pytest.fixture
def templates_dir(tmp_path, monkeypatch):
    """Set up a temporary templates directory with test fixtures."""
    monkeypatch.setenv("COMFYCLAUDE_TEMPLATES_DIR", str(tmp_path))
    monkeypatch.setenv("COMFYUI_URL", "http://test-comfyui:8188")
    importlib.reload(comfyclaude.config)
    importlib.reload(comfyclaude.comfyui)
    return tmp_path

def write_template(templates_dir, name, workflow, meta):
    """Helper to write template files to the test directory."""
    (templates_dir / f"{name}.json").write_text(json.dumps(workflow), encoding="utf-8")
    (templates_dir / f"{name}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
```

**Sample test workflow for fixtures:**
```python
SAMPLE_WORKFLOW = {
    "3": {
        "class_type": "KSampler",
        "inputs": {"seed": 0, "steps": 4, "cfg": 1.0, "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "sampler_name": "euler", "scheduler": "simple", "latent_image": ["5", 0], "denoise": 1.0}
    },
    "5": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 1024, "height": 1024, "batch_size": 1}
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "", "clip": ["4", 1]}
    }
}

SAMPLE_META = {
    "name": "test_template",
    "model": "test-model",
    "description": "Test template",
    "expected_duration": "5 seconds",
    "inputs": {
        "prompt": {"node_id": "6", "field": "text", "type": "required", "description": "Prompt text"}
    },
    "aspect_ratios": {
        "1:1": {"width": 1024, "height": 1024},
        "16:9": {"width": 1344, "height": 768}
    },
    "resolution_nodes": [
        {"node_id": "5", "width_field": "width", "height_field": "height"}
    ]
}
```

**Required test cases:**

- `test_queue_prompt_success` — Write template to tmp_path, mock `POST /prompt` returning `{"prompt_id": "abc-123"}`, call `queue_prompt("test_template", {"prompt": "hello"})`, assert `{"status": "success", "prompt_id": "abc-123"}`
- `test_input_injection` — Submit and verify the mocked request body has `text: "hello"` in node 6
- `test_seed_randomization` — Submit and verify node 3's `seed` is no longer 0. Run twice and assert different seeds (probabilistically; or mock `random.randint`)
- `test_aspect_ratio_injection` — Call with `aspect_ratio="16:9"`, verify node 5 has `width: 1344, height: 768`
- `test_default_resolution_when_no_aspect_ratio` — Call without aspect_ratio, verify node 5 retains `width: 1024, height: 1024`
- `test_unreachable_comfyui_returns_transient_error` — Mock `ConnectError`, assert `{"status": "error", "error_type": "unreachable", "retry_suggested": True, ...}`
- `test_missing_template_returns_terminal_error` — Call with nonexistent template, assert `{"status": "error", "error_type": "invalid_inputs", "retry_suggested": False, ...}`
- `test_invalid_aspect_ratio_returns_terminal_error` — Call with unsupported aspect ratio, assert terminal error with "invalid_inputs"
- `test_missing_required_input_returns_terminal_error` — Call without providing "prompt" input, assert terminal error
- `test_comfyui_400_returns_terminal_error` — Mock 400 response, assert terminal error with "invalid_workflow"
- `test_mcp_queue_prompt_registered` — Verify `queue_prompt` appears in server's tool list

**All tests must be async** — use `@pytest.mark.anyio` or the project's established async test pattern.

### Anti-Pattern Prevention

- Do NOT import from `comfyclaude.templates` in `comfyui.py` — `comfyui.py` reads template files directly. Per architecture, `templates.py` and `comfyui.py` do not import each other.
- Do NOT create a persistent `httpx.AsyncClient` at module level — create per-request. Persistent client is Phase 2.
- Do NOT cache loaded templates — no caching required per NFR4 (filesystem scan per call is sufficient for single-user use).
- Do NOT validate workflow JSON structure beyond "is it a dict" — meta-only validation per architecture. Cross-reference validation is Phase 2.
- Do NOT add `check_job` or `get_image` tools — those are Story 2.3 and 2.4.
- Do NOT modify the starter template files — the `seed: 0` is correct as shipped; `_randomize_seeds()` handles it at runtime.
- Do NOT construct error dicts manually — use `terminal_error()` / `transient_error()` from `comfyclaude.errors`.
- Do NOT log with `print()` — use `logging.getLogger(__name__)`.
- Do NOT add path traversal protection to template loading — that's Story 3.1 scope.

### Deferred Work Awareness

From prior stories (tracked in `deferred-work.md`):
- **Path traversal in template reads** — `template_name` used directly in path construction. Story 3.1 scope. `comfyui.py` follows the same pattern as `templates.py` for consistency.
- **TEMPLATES_DIR relative path** — `./templates` is CWD-dependent. Accepted for MVP.
- **Config import-time evaluation** — Tests must reload both `config` and `comfyui` modules when overriding env vars.

### Project Structure Notes

- `comfyclaude/comfyui.py` is the ComfyUI HTTP boundary per architecture. It will grow in Stories 2.3 (check_job/polling) and 2.4 (get_image) but this story only implements `queue_prompt` and its helpers.
- Tests go in `tests/test_comfyui.py` — same file will be extended by Stories 2.3 and 2.4.

### References

- [Source: _bmad-output/planning-artifacts/architecture.md#Architectural Boundaries] — ComfyUI HTTP Boundary (comfyui.py)
- [Source: _bmad-output/planning-artifacts/architecture.md#Async Architecture] — async throughout, httpx.AsyncClient
- [Source: _bmad-output/planning-artifacts/architecture.md#Tool Return Format] — dict with status field
- [Source: _bmad-output/planning-artifacts/architecture.md#Error Construction Pattern] — transient_error/terminal_error usage
- [Source: _bmad-output/planning-artifacts/architecture.md#Tool Docstring Pattern] — Rich docstrings for Claude Code
- [Source: _bmad-output/planning-artifacts/architecture.md#Test Organization] — tests/ directory, respx for HTTP mocking
- [Source: _bmad-output/planning-artifacts/epics.md#Story 2.2] — Acceptance criteria source
- [Source: _bmad-output/planning-artifacts/prd.md#FR9] — Submit generation job
- [Source: _bmad-output/planning-artifacts/prd.md#FR10] — Input injection into workflow nodes
- [Source: _bmad-output/planning-artifacts/prd.md#FR11] — Seed randomization across all nodes
- [Source: _bmad-output/planning-artifacts/prd.md#FR12] — Aspect ratio mapping to dimensions
- [Source: _bmad-output/planning-artifacts/prd.md#FR13] — Submit workflow to ComfyUI /prompt API
- [Source: _bmad-output/planning-artifacts/prd.md#NFR1] — 30-second HTTP timeout
- [Source: _bmad-output/implementation-artifacts/2-1-template-discovery-and-starter-templates.md] — Previous story context, template meta schema
- [Source: _bmad-output/implementation-artifacts/1-2-fastmcp-server-with-stdio-transport-and-startup-validation.md] — Server.py mcp instance, httpx patterns
- [Source: _bmad-output/implementation-artifacts/deferred-work.md] — seed:0 deferred item resolved by this story
- [Source: templates/flux2_klein.json] — Starter workflow structure (node IDs, seed field location)
- [Source: templates/flux2_klein.meta.json] — Meta schema (inputs, aspect_ratios, resolution_nodes)

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

- Test `test_mcp_queue_prompt_registered` initially failed due to Python scoping issue with `comfyclaude` module name — resolved by using aliased local imports

### Completion Notes List

- Created `comfyclaude/comfyui.py` with `queue_prompt()`, `_inject_inputs()`, `_randomize_seeds()`, `_inject_resolution()` — all following the architecture spec exactly
- Registered `queue_prompt` MCP tool in `server.py` as thin wrapper delegating to `comfyui.queue_prompt()`
- All 11 tests pass covering: success path, input injection, seed randomization, aspect ratio injection, default resolution, unreachable ComfyUI (transient error), missing template, invalid aspect ratio, missing required input, ComfyUI 400 response, and MCP tool registration
- Full regression suite: 39/39 tests pass (no regressions)
- Seed randomization resolves deferred `seed: 0` issue from Story 2.1

### Change Log

- 2026-03-29: Implemented job submission with input injection and seed randomization (Story 2.2)

### File List

- `comfyclaude/comfyui.py` (new) — ComfyUI HTTP client with queue_prompt and workflow preparation helpers
- `comfyclaude/server.py` (modified) — Added queue_prompt MCP tool registration
- `tests/test_comfyui.py` (new) — 11 tests for job submission with respx-mocked HTTP
