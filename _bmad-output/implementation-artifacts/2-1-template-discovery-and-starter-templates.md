# Story 2.1: Template Discovery & Starter Templates

Status: done

## Story

As a developer using Claude Code,
I want to browse available workflow templates and inspect their metadata,
So that Claude Code can select the right template for my intent without me having to know template details.

## Acceptance Criteria

1. **Given** the `templates/` directory contains starter templates
   **When** I call the `list_templates` MCP tool
   **Then** it returns a list of all templates with summary metadata: name, model, description, supported aspect ratios, and expected duration (FR1)

2. **Given** a template named `flux2_klein` exists
   **When** I call the `get_template` MCP tool with `template_name: "flux2_klein"`
   **Then** it returns the full metadata including required inputs, optional inputs with defaults, and supported aspect ratios (FR2)

3. **Given** the tool descriptions are registered in FastMCP
   **When** Claude Code reads the tool descriptions
   **Then** they contain enough context for Claude Code to determine the appropriate template for a user's intent (FR3)

4. **Given** the project is freshly cloned
   **When** I inspect the `templates/` directory
   **Then** it contains `flux2_klein.json`, `flux2_klein.meta.json`, `flux2_klein_ultrawide.json`, and `flux2_klein_ultrawide.meta.json` (FR29)

5. **Given** a template name does not exist
   **When** I call `get_template` with that name
   **Then** it returns a terminal error with `error_type: "invalid_inputs"` and a clear message

6. **Given** the story is complete
   **When** I run `uv run pytest tests/test_templates.py`
   **Then** all template discovery tests pass

## Tasks / Subtasks

- [x] Task 1: Create `comfyclaude/templates.py` with discovery functions (AC: #1, #2, #5)
  - [x] 1.1 Create `comfyclaude/templates.py` with `list_templates()` async function
  - [x] 1.2 `list_templates()` scans `TEMPLATES_DIR` for `.meta.json` files, reads each, returns list of summary dicts
  - [x] 1.3 Summary dict fields: `name`, `model`, `description`, `aspect_ratios` (list of supported labels), `expected_duration`
  - [x] 1.4 Create `get_template()` async function accepting `template_name: str`
  - [x] 1.5 `get_template()` reads `{template_name}.meta.json` from `TEMPLATES_DIR`, returns full metadata dict
  - [x] 1.6 `get_template()` returns `terminal_error("invalid_inputs", ...)` if template does not exist
  - [x] 1.7 Both functions handle empty templates directory gracefully (return empty list / appropriate error)
- [x] Task 2: Register MCP tools in `server.py` (AC: #3)
  - [x] 2.1 Add `@mcp.tool()` for `list_templates` with rich docstring describing what it returns and when to use it
  - [x] 2.2 Add `@mcp.tool()` for `get_template` with rich docstring including parameter guidance
  - [x] 2.3 Tool functions in `server.py` delegate to `templates.py` functions
- [x] Task 3: Create starter template files in `templates/` (AC: #4)
  - [x] 3.1 Create `templates/flux2_klein.json` — workflow JSON for FLUX.1 [schnell] model
  - [x] 3.2 Create `templates/flux2_klein.meta.json` — metadata sidecar
  - [x] 3.3 Create `templates/flux2_klein_ultrawide.json` — ultrawide variant workflow JSON
  - [x] 3.4 Create `templates/flux2_klein_ultrawide.meta.json` — ultrawide metadata sidecar
- [x] Task 4: Write tests in `tests/test_templates.py` (AC: #6)
  - [x] 4.1 Test `list_templates` returns all templates with correct summary fields
  - [x] 4.2 Test `list_templates` returns empty list when no templates exist
  - [x] 4.3 Test `get_template` returns full metadata for existing template
  - [x] 4.4 Test `get_template` returns terminal error for non-existent template
  - [x] 4.5 Test MCP tool registration (tools appear in server's tool list)

## Dev Notes

### Architecture Compliance

**Module boundaries** — Per architecture, `templates.py` is the only module that reads/writes template files. It owns the template filesystem boundary. `server.py` registers MCP tools and delegates to `templates.py`. These two modules do NOT import each other's internals — `server.py` imports functions from `templates.py`, never the reverse.

**Tool return format** — All MCP tools return dicts with a `status` field:
- Success: `{"status": "success", ...data}`
- Error: Use `terminal_error()` / `transient_error()` from `comfyclaude.errors`

**Tool registration** — `server.py` is the only module that imports FastMCP and registers tools via `@mcp.tool()`. All `@mcp.tool()` functions live in `server.py`. They are thin orchestration wrappers that call into `templates.py`.

### Technical Requirements

**Template file format** — Each template is a pair of files in `TEMPLATES_DIR`:
- `{name}.json` — The ComfyUI workflow JSON (API format, not UI format)
- `{name}.meta.json` — Metadata sidecar with structure below

**`.meta.json` schema:**

```json
{
  "name": "flux2_klein",
  "model": "FLUX.1 [schnell]",
  "description": "Fast FLUX image generation using the schnell (fast) model. Good for quick iterations and creative exploration. Generates 1024x1024 by default.",
  "expected_duration": "10-20 seconds",
  "inputs": {
    "prompt": {
      "node_id": "6",
      "field": "text",
      "type": "required",
      "description": "Text prompt describing the image to generate"
    }
  },
  "aspect_ratios": {
    "1:1": {"width": 1024, "height": 1024},
    "16:9": {"width": 1344, "height": 768},
    "9:16": {"width": 768, "height": 1344},
    "4:3": {"width": 1152, "height": 896},
    "3:4": {"width": 896, "height": 1152}
  },
  "resolution_nodes": [
    {"node_id": "5", "width_field": "width", "height_field": "height"}
  ]
}
```

**`flux2_klein_ultrawide` differs by:**
- `name`: `"flux2_klein_ultrawide"`
- `description`: Emphasizes ultrawide/cinematic aspect ratios
- `aspect_ratios`: Includes `"21:9"` (1536x640) and `"32:9"` (2048x576) in addition to standard ratios
- Same `inputs`, `model`, `resolution_nodes` structure

**`list_templates()` implementation:**

```python
import json
import logging
from pathlib import Path
from comfyclaude.config import TEMPLATES_DIR

logger = logging.getLogger(__name__)

async def list_templates() -> dict:
    templates_path = Path(TEMPLATES_DIR)
    if not templates_path.is_dir():
        return {"status": "success", "templates": []}

    templates = []
    for meta_file in sorted(templates_path.glob("*.meta.json")):
        try:
            meta = json.loads(meta_file.read_text())
            templates.append({
                "name": meta["name"],
                "model": meta["model"],
                "description": meta["description"],
                "aspect_ratios": list(meta.get("aspect_ratios", {}).keys()),
                "expected_duration": meta.get("expected_duration", "unknown"),
            })
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("Skipping invalid template %s: %s", meta_file.name, exc)
    return {"status": "success", "templates": templates}
```

**`get_template()` implementation:**

```python
async def get_template(template_name: str) -> dict:
    meta_path = Path(TEMPLATES_DIR) / f"{template_name}.meta.json"
    if not meta_path.is_file():
        return terminal_error("invalid_inputs", f"Template '{template_name}' not found")

    meta = json.loads(meta_path.read_text())
    return {"status": "success", **meta}
```

**MCP tool registration in `server.py`:**

```python
from comfyclaude import templates

@mcp.tool()
async def list_templates() -> dict:
    """List all available workflow templates with summary metadata.

    Returns template names, models, descriptions, supported aspect ratios,
    and expected generation duration. Use this to discover what image generation
    workflows are available before calling queue_prompt.

    Each template targets a specific model and supports different aspect ratios.
    Choose the template that best matches the user's intent based on the model
    and description.
    """
    return await templates.list_templates()

@mcp.tool()
async def get_template(template_name: str) -> dict:
    """Inspect a specific template's full metadata including input definitions.

    Returns the complete template configuration: model, description, required
    and optional inputs with their node mappings, supported aspect ratios with
    exact pixel dimensions, and resolution node definitions.

    Call this after list_templates to get detailed input requirements before
    calling queue_prompt. The inputs field shows exactly what parameters the
    template accepts (e.g., prompt text, negative prompt).
    """
    return await templates.get_template(template_name)
```

### Starter Template Content

**Workflow JSON structure** — The `.json` files contain ComfyUI API-format workflows. These are NOT the UI-format workflows (no `extra` or `links` fields). They are the minimal node graph that ComfyUI's `/prompt` endpoint accepts.

**`flux2_klein.json` content** — A minimal FLUX.1 schnell workflow with these nodes:
- Node 5: `EmptyLatentImage` (width=1024, height=1024, batch_size=1) — resolution node
- Node 6: `CLIPTextEncode` (text="") — prompt input node
- Node 3: `KSampler` (seed=0, steps=4, cfg=1.0, sampler_name="euler", scheduler="simple") — sampler
- Node 4: `CheckpointLoaderSimple` (ckpt_name="flux1-schnell-fp8.safetensors") — model loader
- Node 8: `VAEDecode` — decode latent to image
- Node 9: `SaveImage` (filename_prefix="ComfyUI") — output

The exact node IDs and connections must match the `node_id` references in the `.meta.json`. Node 6 is the prompt input (`inputs.prompt.node_id: "6"`), node 5 is the resolution node (`resolution_nodes[0].node_id: "5"`).

**`flux2_klein_ultrawide.json`** — Same workflow structure as `flux2_klein.json` but with default `EmptyLatentImage` set to width=1344, height=768 (16:9 default). All other nodes are identical.

**CRITICAL: Workflow JSON must be valid ComfyUI API format.** Each node is a key in a dict, with `class_type` and `inputs` fields. The `inputs` field references other nodes via `[node_id, output_index]` tuples. If you are unsure of exact ComfyUI node structures, use the pattern from cenobite-agent as reference.

### Previous Story Intelligence

**Story 1.1 established:**
- `comfyclaude/config.py` with `TEMPLATES_DIR = os.environ.get("COMFYCLAUDE_TEMPLATES_DIR", "./templates")` — use this constant for template directory path
- `comfyclaude/errors.py` with `terminal_error()` / `transient_error()` — use for all error responses
- `tests/conftest.py` with `autouse` fixture that reloads `comfyclaude.config` after each test

**Story 1.2 established:**
- `comfyclaude/server.py` with `mcp = FastMCP("comfyclaude", lifespan=lifespan)` — add `@mcp.tool()` registrations to this file
- `main.py` imports `mcp` from `server.py` — no changes needed
- respx test pattern for mocking httpx (though this story's tests are filesystem-based, not HTTP-based)

**Review findings carried forward:**
- Config constants evaluated at import time — tests using different TEMPLATES_DIR must monkeypatch env var + reload config module
- The `reload_config` autouse fixture in conftest.py handles cleanup

**Git patterns from Epic 1:**
- FastMCP 3.1.1 is the installed version
- Clean separation: source in `comfyclaude/`, tests in `tests/`
- Tests use `pytest` with `monkeypatch`

### Library & Framework Requirements

| Package | Version (installed) | Usage in this story |
|---------|-------------------|---------------------|
| fastmcp | 3.1.1 | `@mcp.tool()` decorator for registering list_templates and get_template |
| pytest | 9.0.2 | Test framework |

**Key FastMCP API (v3.1.1):**
- `@mcp.tool()` — Decorator to register an async function as an MCP tool. Function name becomes tool name. Docstring becomes tool description. Type hints generate parameter schema.
- Tool functions must return a serializable value (dict, str, list). FastMCP handles JSON-RPC wrapping.

**No new dependencies needed for this story.** Template discovery is filesystem-based (pathlib + json stdlib). No HTTP calls.

### File Structure Requirements

**Create:**
```
comfyclaude/templates.py     # Template discovery functions (list_templates, get_template)
templates/                   # Starter templates directory
  flux2_klein.json           # FLUX.1 schnell workflow JSON
  flux2_klein.meta.json      # FLUX.1 schnell metadata
  flux2_klein_ultrawide.json # FLUX.1 schnell ultrawide workflow JSON
  flux2_klein_ultrawide.meta.json  # FLUX.1 schnell ultrawide metadata
tests/test_templates.py      # Template discovery tests
```

**Modify:**
```
comfyclaude/server.py        # Add @mcp.tool() for list_templates and get_template
```

**Do NOT create or modify:**
- `comfyclaude/comfyui.py` — Story 2.2
- `comfyclaude/init.py` — Story 4.1
- `comfyclaude/assets/` — Story 4.1 (init command bundles assets separately)
- `main.py` — No changes needed
- `comfyclaude/config.py` — No changes needed
- `comfyclaude/errors.py` — No changes needed

### Testing Requirements

Tests in `tests/test_templates.py` using filesystem fixtures (no HTTP mocking needed):

**Use `tmp_path` pytest fixture** to create temporary template directories with test fixtures. Do NOT test against the real `templates/` directory — tests must be isolated.

**Required test cases:**

- `test_list_templates_returns_all` — Create 2 templates in tmp_path, set TEMPLATES_DIR to tmp_path, call `list_templates()`, assert both returned with correct summary fields (name, model, description, aspect_ratios as list of labels, expected_duration)
- `test_list_templates_empty_directory` — Set TEMPLATES_DIR to empty tmp_path, call `list_templates()`, assert `{"status": "success", "templates": []}` returned
- `test_list_templates_missing_directory` — Set TEMPLATES_DIR to non-existent path, call `list_templates()`, assert `{"status": "success", "templates": []}` returned
- `test_list_templates_skips_invalid_meta` — Create one valid and one malformed .meta.json, assert only valid template returned
- `test_get_template_returns_full_metadata` — Create template in tmp_path, call `get_template("template_name")`, assert full metadata returned with status "success"
- `test_get_template_not_found` — Call `get_template("nonexistent")`, assert terminal error with error_type "invalid_inputs"
- `test_mcp_tools_registered` — Import `mcp` from server, verify `list_templates` and `get_template` are in the registered tools

**Testing pattern for TEMPLATES_DIR override:**

```python
import importlib
import comfyclaude.config
import comfyclaude.templates

@pytest.fixture
def templates_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("COMFYCLAUDE_TEMPLATES_DIR", str(tmp_path))
    importlib.reload(comfyclaude.config)
    importlib.reload(comfyclaude.templates)  # templates.py imports TEMPLATES_DIR
    return tmp_path
```

Note: `comfyclaude.templates` must be reloaded after config to pick up the new `TEMPLATES_DIR` value, since it imports `TEMPLATES_DIR` at module level. The existing `reload_config` autouse fixture in conftest.py handles cleanup after the test.

**All tests must be async** — use `@pytest.mark.anyio` or the project's established async test pattern. `list_templates()` and `get_template()` are async functions.

### Anti-Pattern Prevention

- Do NOT read workflow `.json` files in `list_templates()` — only read `.meta.json` for discovery. Workflow JSON is large and unnecessary for listing.
- Do NOT validate template content in this story — meta-only validation is Story 3.1's scope. This story just reads and returns metadata as-is.
- Do NOT add `add_template`, `update_template`, or `delete_template` — those are Story 3.1/3.2.
- Do NOT create `comfyclaude/comfyui.py` — that's Story 2.2 (job submission).
- Do NOT hardcode template paths — always use `TEMPLATES_DIR` from config.
- Do NOT use synchronous file I/O in tool functions and then wrap in `asyncio.to_thread` — just use synchronous `pathlib` reads directly within the async function. For single-user local use, the I/O is negligible and `asyncio.to_thread` adds unnecessary complexity.
- Do NOT construct error dicts manually — use `terminal_error()` from `comfyclaude.errors`.
- Do NOT log with `print()` — use `logging.getLogger(__name__)`.
- Do NOT add path traversal protection in `get_template()` — that's Story 3.1's scope (template management/write operations). Read-only discovery doesn't need it for MVP.

### Deferred Work Awareness

From Epic 1 deferred work:
- Config constants evaluated at import time — `templates.py` will import `TEMPLATES_DIR` at module level. Tests must reload both `config` and `templates` modules.
- Relative path `./templates` is cwd-dependent — acceptable for MVP. The server is expected to run from the project root.

### Project Structure Notes

- `templates/` directory at project root is the dev-time template storage per architecture doc. This is where starter templates live when running the server directly from the repo.
- `comfyclaude/assets/starter-templates/` (Story 4.1) is where copies will live for the init command. This story only creates the `templates/` directory.
- The `templates/` directory should NOT be gitignored — starter templates are part of the project distribution.

### References

- [Source: _bmad-output/planning-artifacts/architecture.md#Module Structure & Distribution] — templates.py module, templates/ directory
- [Source: _bmad-output/planning-artifacts/architecture.md#Architectural Boundaries] — Template Filesystem Boundary (templates.py)
- [Source: _bmad-output/planning-artifacts/architecture.md#Tool Docstring Pattern] — FastMCP docstring conventions
- [Source: _bmad-output/planning-artifacts/architecture.md#Tool Return Format] — Success/error dict shape
- [Source: _bmad-output/planning-artifacts/architecture.md#Error Construction Pattern] — terminal_error() usage
- [Source: _bmad-output/planning-artifacts/architecture.md#Test Organization] — test placement, naming conventions
- [Source: _bmad-output/planning-artifacts/epics.md#Story 2.1] — Acceptance criteria source
- [Source: _bmad-output/planning-artifacts/prd.md#FR1] — List templates with summary metadata
- [Source: _bmad-output/planning-artifacts/prd.md#FR2] — Inspect template full metadata
- [Source: _bmad-output/planning-artifacts/prd.md#FR3] — Template selection from rich descriptions
- [Source: _bmad-output/planning-artifacts/prd.md#FR29] — Two starter templates ship out of box
- [Source: _bmad-output/implementation-artifacts/1-1-package-structure-configuration-and-error-types.md] — Config and error patterns
- [Source: _bmad-output/implementation-artifacts/1-2-fastmcp-server-with-stdio-transport-and-startup-validation.md] — Server.py mcp instance, test patterns
- [Source: _bmad-output/implementation-artifacts/deferred-work.md] — Known deferred items

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

- FastMCP 3.1.1 `list_tools()` is async — test_mcp_tools_registered updated to use `@pytest.mark.anyio` and `await mcp.list_tools()`
- FastMCP 3.1.1 tool introspection via `await mcp.list_tools()` returning objects with `.name` attribute (no `_tool_manager`)

### Completion Notes List

- Created `comfyclaude/templates.py` with `list_templates()` and `get_template()` async functions following architecture boundaries
- `list_templates()` scans TEMPLATES_DIR for `.meta.json` files, returns summary dicts with name, model, description, aspect_ratios (labels), expected_duration
- `get_template()` returns full metadata or `terminal_error("invalid_inputs", ...)` for missing templates
- Both functions handle missing/empty directories gracefully
- Registered `list_templates` and `get_template` as MCP tools in `server.py` with rich docstrings for Claude Code discoverability
- Created 4 starter template files: flux2_klein and flux2_klein_ultrawide (workflow JSON + metadata sidecar each)
- Ultrawide variant includes additional 21:9 and 32:9 aspect ratios
- 7 tests covering all required cases: list all, empty dir, missing dir, skip invalid meta, get full metadata, not found error, MCP tool registration
- All 95 tests pass (0 regressions)

### Review Findings

- [x] [Review][Patch] `get_template` missing JSON error handling — `json.loads(meta_path.read_text())` has no try/except; malformed .meta.json raises unhandled `json.JSONDecodeError` instead of returning `terminal_error` [`comfyclaude/templates.py:39`]
- [x] [Review][Patch] Status key shadowing in `get_template` response — `{"status": "success", **meta}` allows a `"status"` key in the JSON file to overwrite the hardcoded success status; reorder to `{**meta, "status": "success"}` [`comfyclaude/templates.py:40`]
- [x] [Review][Patch] `OSError`/`PermissionError` not caught in file reads — `read_text()` in both `list_templates` and `get_template` is not guarded against OS-level errors; add `OSError` to exception handler [`comfyclaude/templates.py:20,39`]
- [x] [Review][Patch] `read_text()` missing explicit encoding — should use `read_text(encoding="utf-8")` to avoid `UnicodeDecodeError` on non-UTF-8 platforms [`comfyclaude/templates.py:20,39`]
- [x] [Review][Patch] Missing `KeyError` branch test in `test_list_templates_skips_invalid_meta` — test only exercises invalid JSON path; add case for valid JSON missing a required key (e.g., missing `"name"`) [`tests/test_templates.py`]
- [x] [Review][Defer] Path traversal in `get_template` — `template_name` used directly in path construction without containment check; Story 3.1 scope [`comfyclaude/templates.py:35`] — deferred, pre-existing
- [x] [Review][Defer] `seed: 0` hardcoded in workflow JSON — static seed produces identical outputs; Story 2.2 (seed randomization) will handle this [`templates/flux2_klein.json`] — deferred, pre-existing
- [x] [Review][Defer] Symlinks in templates directory followed without guard — `glob("*.meta.json")` follows symlinks to files outside `TEMPLATES_DIR`; Story 3.1 scope [`comfyclaude/templates.py:18`] — deferred, pre-existing
- [x] [Review][Defer] `TEMPLATES_DIR` default is a relative path — `"./templates"` is CWD-dependent; explicitly accepted for MVP, expected to run from project root [`comfyclaude/config.py`] — deferred, pre-existing
- [x] [Review][Defer] Test module reload side effects — `importlib.reload(comfyclaude.server)` in `test_mcp_tools_registered` may cause tool double-registration or reset `TEMPLATES_DIR`; passes in practice (95 tests green) [`tests/test_templates.py:120`] — deferred, pre-existing

### Change Log

- 2026-03-29: Implemented Story 2.1 — template discovery functions, MCP tool registration, starter templates, and tests

### File List

- comfyclaude/templates.py (new)
- comfyclaude/server.py (modified)
- templates/flux2_klein.json (new)
- templates/flux2_klein.meta.json (new)
- templates/flux2_klein_ultrawide.json (new)
- templates/flux2_klein_ultrawide.meta.json (new)
- tests/test_templates.py (new)
