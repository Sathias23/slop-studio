# Story 3.1: Add & Update Workflow Templates with Validation

Status: done

## Story

As a developer using Claude Code,
I want to add new workflow templates and update existing ones from exported ComfyUI workflows,
So that I can use any ComfyUI workflow conversationally without manually editing JSON files.

## Acceptance Criteria

1. **Given** a valid workflow JSON and metadata (model, description, inputs, aspect ratios, duration)
   **When** I call the `add_template` MCP tool with `name`, `workflow_json`, and `metadata`
   **Then** it saves `{name}.json` and `{name}.meta.json` to the templates directory (FR4)

2. **Given** an existing template named `sdxl_turbo`
   **When** I call the `update_template` MCP tool with `name: "sdxl_turbo"` and new workflow JSON and/or metadata
   **Then** it overwrites the existing `.json` and/or `.meta.json` files (FR5)

3. **Given** a template name containing path traversal characters (`/`, `..`, or leading `.`)
   **When** I call `add_template` or `update_template` with that name
   **Then** it returns a terminal error with `error_type: "invalid_inputs"` and rejects the operation (FR8)

4. **Given** metadata is missing required fields (e.g., no input definitions, no model name)
   **When** I call `add_template` or `update_template`
   **Then** it returns a terminal error with `error_type: "invalid_inputs"` describing which fields are missing (FR7)

5. **Given** metadata defines input node IDs that are structurally invalid or resolution nodes that are malformed
   **When** I call `add_template` or `update_template`
   **Then** it validates the meta structure and returns a terminal error if validation fails (FR7 -- meta-only validation per Architecture)

6. **Given** a template was just added via `add_template`
   **When** I call `list_templates`
   **Then** the new template appears immediately in the listing

7. **Given** the story is complete
   **When** I run `uv run pytest tests/test_templates.py`
   **Then** all add/update and validation tests pass

## Tasks / Subtasks

- [x] Task 1: Add `_validate_template_name` to `comfyclaude/templates.py` (AC: #3)
  - [x] 1.1 Implement `_validate_template_name(name: str) -> str | None` that returns an error message if the name contains `/`, `..`, starts with `.`, or is empty; returns `None` if valid
  - [x] 1.2 Also apply this validation to the existing `get_template` function (closes deferred path traversal item from Story 2.1)

- [x] Task 2: Add `_validate_metadata` to `comfyclaude/templates.py` (AC: #4, #5)
  - [x] 2.1 Implement `_validate_metadata(metadata: dict) -> str | None` that returns an error message if validation fails, `None` if valid
  - [x] 2.2 Validate required top-level fields: `name` (str), `model` (str), `description` (str)
  - [x] 2.3 Validate `inputs` is a dict; each value must have `node_id` (str) and `field` (str) -- `type` and `description` are optional
  - [x] 2.4 Validate `aspect_ratios` if present: dict of label -> `{"width": int, "height": int}`
  - [x] 2.5 Validate `resolution_nodes` if present: list of dicts each with `node_id` (str), `width_field` (str), `height_field` (str)
  - [x] 2.6 Do NOT validate workflow JSON structure beyond "is it a dict" (architecture: meta-only validation for MVP)

- [x] Task 3: Implement `add_template` in `comfyclaude/templates.py` (AC: #1, #3, #4, #5, #6)
  - [x] 3.1 Implement `async def add_template(name: str, workflow_json: dict, metadata: dict) -> dict`
  - [x] 3.2 Validate name with `_validate_template_name`; return `terminal_error("invalid_inputs", ...)` on failure
  - [x] 3.3 Check template does NOT already exist (both `.json` and `.meta.json`); return `terminal_error("invalid_inputs", ...)` if it does
  - [x] 3.4 Validate `workflow_json` is a dict; return `terminal_error("invalid_inputs", "workflow_json must be a JSON object")` if not
  - [x] 3.5 Ensure `metadata["name"]` is set to match the `name` parameter (override if mismatched)
  - [x] 3.6 Validate metadata with `_validate_metadata`; return `terminal_error("invalid_inputs", ...)` on failure
  - [x] 3.7 Write `{name}.json` and `{name}.meta.json` atomically (write both or neither -- write to temp files then rename, or write workflow first, meta second, delete workflow on meta failure)
  - [x] 3.8 Return `{"status": "success", "name": name, "message": "Template '{name}' added"}`
  - [x] 3.9 Catch `OSError` on write -> `transient_error("storage_error", ...)`

- [x] Task 4: Implement `update_template` in `comfyclaude/templates.py` (AC: #2, #3, #4, #5)
  - [x] 4.1 Implement `async def update_template(name: str, workflow_json: dict | None = None, metadata: dict | None = None) -> dict`
  - [x] 4.2 Validate name with `_validate_template_name`
  - [x] 4.3 Check template DOES already exist (at least `.meta.json`); return `terminal_error("invalid_inputs", ...)` if not found
  - [x] 4.4 Require at least one of `workflow_json` or `metadata` is provided; return `terminal_error("invalid_inputs", ...)` if both are None
  - [x] 4.5 If `workflow_json` provided: validate is a dict, write `{name}.json`
  - [x] 4.6 If `metadata` provided: ensure `metadata["name"]` matches `name`, validate with `_validate_metadata`, write `{name}.meta.json`
  - [x] 4.7 Return `{"status": "success", "name": name, "message": "Template '{name}' updated"}`
  - [x] 4.8 Catch `OSError` on write -> `transient_error("storage_error", ...)`

- [x] Task 5: Register MCP tools in `comfyclaude/server.py` (AC: #1, #2)
  - [x] 5.1 Add `@mcp.tool()` for `add_template` with rich docstring
  - [x] 5.2 Add `@mcp.tool()` for `update_template` with rich docstring
  - [x] 5.3 Both tools delegate to `templates.add_template()` / `templates.update_template()`

- [x] Task 6: Write tests in `tests/test_templates.py` (AC: #7)
  - [x] 6.1 Test `add_template` creates both `.json` and `.meta.json` files
  - [x] 6.2 Test `add_template` returns success with name
  - [x] 6.3 Test `add_template` with path traversal name (`../evil`, `foo/bar`, `.hidden`) returns `invalid_inputs` error
  - [x] 6.4 Test `add_template` with empty name returns `invalid_inputs` error
  - [x] 6.5 Test `add_template` when template already exists returns `invalid_inputs` error
  - [x] 6.6 Test `add_template` with missing required meta fields returns `invalid_inputs` error listing missing fields
  - [x] 6.7 Test `add_template` with invalid input definition (missing node_id) returns error
  - [x] 6.8 Test `add_template` with invalid resolution_nodes structure returns error
  - [x] 6.9 Test `add_template` with invalid aspect_ratios structure returns error
  - [x] 6.10 Test `add_template` with non-dict workflow_json returns error
  - [x] 6.11 Test `add_template` then `list_templates` shows new template immediately
  - [x] 6.12 Test `update_template` overwrites existing files
  - [x] 6.13 Test `update_template` with only workflow_json updates just the `.json` file
  - [x] 6.14 Test `update_template` with only metadata updates just the `.meta.json` file
  - [x] 6.15 Test `update_template` for nonexistent template returns `invalid_inputs` error
  - [x] 6.16 Test `update_template` with path traversal name returns `invalid_inputs` error
  - [x] 6.17 Test `update_template` with neither workflow_json nor metadata returns error
  - [x] 6.18 Test `get_template` with path traversal name returns `invalid_inputs` error (backfill)
  - [x] 6.19 Test MCP tools `add_template` and `update_template` are registered
  - [x] 6.20 Test `add_template` with storage error returns `storage_error` transient error

### Review Findings

- [x] [Review][Decision] `update_template` partial failure — split-state accepted; update is best-effort, caller sees error and can retry. No rollback required.
- [x] [Review][Patch] Caller-supplied `metadata` dict mutated in place via `metadata["name"] = name` before validation [comfyclaude/templates.py:116,174]
- [x] [Review][Patch] `add_template` cleanup `unlink` calls can raise `OSError` and propagate uncaught [comfyclaude/templates.py:136-137]
- [x] [Review][Patch] `isinstance(True, int)` is `True` in Python — bool values silently pass `aspect_ratios` `width`/`height` int check [comfyclaude/templates.py:52-53]
- [x] [Review][Patch] `_validate_template_name` raises `AttributeError` if `name` is not a `str` (e.g. `None` or `int`) [comfyclaude/templates.py:13]
- [x] [Review][Patch] `add_template` docstring in `server.py` states `inputs` is required; spec Dev Notes classify it as optional (structurally validated if present) [comfyclaude/server.py:79]
- [x] [Review][Patch] `test_update_template_rejects_bad_names` parametrize is missing `""`, `"   "`, `"a..b"` cases present in the `add_template` equivalent [tests/test_templates.py:375]
- [x] [Review][Defer] `_validate_metadata` "name" required check is dead code — name always injected before validator call [comfyclaude/templates.py:27-30] — deferred, pre-existing
- [x] [Review][Defer] `_validate_template_name` does not block backslash or null bytes — outside FR8 scope [comfyclaude/templates.py:13-22] — deferred, pre-existing
- [x] [Review][Defer] `update_template` silently creates `.json` for broken template state (meta exists, `.json` missing) — not addressed by spec [comfyclaude/templates.py:162-165] — deferred, pre-existing
- [x] [Review][Defer] `test_add_template_storage_error` global `Path.write_text` monkeypatch is fragile — could affect fixture teardown [tests/test_templates.py:259] — deferred, pre-existing
- [x] [Review][Defer] No positive integer validation for `aspect_ratios` `width`/`height` — spec only requires `int` type [comfyclaude/templates.py:52-53] — deferred, pre-existing

## Dev Notes

### Architecture Compliance

**Module boundaries** -- Per architecture, `comfyclaude/templates.py` is the **only** module that reads/writes template files. Both `add_template` and `update_template` belong here alongside the existing `list_templates` and `get_template`. `server.py` registers MCP tools and delegates.

**Tool return format** -- All MCP tools return dicts with a `status` field:
- Success: `{"status": "success", "name": "<template_name>", "message": "..."}`
- Error: Use `terminal_error()` or `transient_error()` from `comfyclaude.errors`

**Validation scope** -- Architecture decision: meta-only validation for MVP. Do NOT validate workflow JSON structure beyond confirming it's a dict. Cross-reference validation (checking that node_ids in meta exist in workflow JSON) is explicitly deferred to Phase 2.

**Path sanitization** -- FR8 requires rejecting template names with `/`, `..`, or leading `.`. This is a template-level security boundary owned by `templates.py`.

### Technical Requirements

**Template file format:**
Templates are stored as pairs in `TEMPLATES_DIR`:
- `{name}.json` -- Raw ComfyUI workflow JSON (opaque dict)
- `{name}.meta.json` -- Structured metadata sidecar

**Required `.meta.json` structure:**
```json
{
  "name": "template_name",
  "model": "model-identifier",
  "description": "Human-readable description",
  "inputs": {
    "<input_name>": {
      "node_id": "<string>",
      "field": "<string>",
      "type": "required|optional",
      "description": "..."
    }
  },
  "aspect_ratios": {
    "<label>": {"width": <int>, "height": <int>}
  },
  "resolution_nodes": [
    {"node_id": "<string>", "width_field": "<string>", "height_field": "<string>"}
  ],
  "expected_duration": "30 seconds"
}
```

**Required fields for validation:** `name` (str), `model` (str), `description` (str)
**Structurally validated if present:** `inputs`, `aspect_ratios`, `resolution_nodes`
**Optional (no validation needed):** `expected_duration`

**Name validation rules (FR8):**
- Reject if name contains `/`
- Reject if name contains `..`
- Reject if name starts with `.`
- Reject if name is empty or whitespace-only

**Implementation approach:**

```python
def _validate_template_name(name: str) -> str | None:
    """Return error message if name is invalid, None if valid."""
    if not name or not name.strip():
        return "Template name cannot be empty"
    if "/" in name:
        return f"Template name cannot contain '/': {name!r}"
    if ".." in name:
        return f"Template name cannot contain '..': {name!r}"
    if name.startswith("."):
        return f"Template name cannot start with '.': {name!r}"
    return None


def _validate_metadata(metadata: dict) -> str | None:
    """Validate meta structure. Returns error message or None."""
    missing = [f for f in ("name", "model", "description") if not isinstance(metadata.get(f), str) or not metadata[f]]
    if missing:
        return f"Missing required metadata fields: {', '.join(missing)}"

    inputs = metadata.get("inputs")
    if inputs is not None:
        if not isinstance(inputs, dict):
            return "inputs must be a JSON object"
        for key, defn in inputs.items():
            if not isinstance(defn, dict):
                return f"Input '{key}' must be a JSON object"
            if not isinstance(defn.get("node_id"), str) or not defn["node_id"]:
                return f"Input '{key}' missing required 'node_id' (string)"
            if not isinstance(defn.get("field"), str) or not defn["field"]:
                return f"Input '{key}' missing required 'field' (string)"

    aspect_ratios = metadata.get("aspect_ratios")
    if aspect_ratios is not None:
        if not isinstance(aspect_ratios, dict):
            return "aspect_ratios must be a JSON object"
        for label, dims in aspect_ratios.items():
            if not isinstance(dims, dict):
                return f"Aspect ratio '{label}' must be a JSON object with width/height"
            if not isinstance(dims.get("width"), int) or not isinstance(dims.get("height"), int):
                return f"Aspect ratio '{label}' requires integer width and height"

    res_nodes = metadata.get("resolution_nodes")
    if res_nodes is not None:
        if not isinstance(res_nodes, list):
            return "resolution_nodes must be a JSON array"
        for i, node in enumerate(res_nodes):
            if not isinstance(node, dict):
                return f"resolution_nodes[{i}] must be a JSON object"
            for field in ("node_id", "width_field", "height_field"):
                if not isinstance(node.get(field), str) or not node[field]:
                    return f"resolution_nodes[{i}] missing required '{field}' (string)"

    return None


async def add_template(name: str, workflow_json: dict, metadata: dict) -> dict:
    """Add a new workflow template with validation."""
    name_err = _validate_template_name(name)
    if name_err:
        return terminal_error("invalid_inputs", name_err)

    if not isinstance(workflow_json, dict):
        return terminal_error("invalid_inputs", "workflow_json must be a JSON object")

    metadata["name"] = name  # Ensure name matches
    meta_err = _validate_metadata(metadata)
    if meta_err:
        return terminal_error("invalid_inputs", meta_err)

    templates_path = Path(TEMPLATES_DIR)
    workflow_path = templates_path / f"{name}.json"
    meta_path = templates_path / f"{name}.meta.json"

    if workflow_path.exists() or meta_path.exists():
        return terminal_error("invalid_inputs",
            f"Template '{name}' already exists. Use update_template to modify it.")

    try:
        templates_path.mkdir(parents=True, exist_ok=True)
        workflow_path.write_text(json.dumps(workflow_json, indent=2), encoding="utf-8")
        meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    except OSError as exc:
        # Clean up partial writes
        workflow_path.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        return transient_error("storage_error", f"Failed to write template '{name}': {exc}")

    logger.info("Template added: %s", name)
    return {"status": "success", "name": name, "message": f"Template '{name}' added"}


async def update_template(
    name: str, workflow_json: dict | None = None, metadata: dict | None = None
) -> dict:
    """Update an existing workflow template."""
    name_err = _validate_template_name(name)
    if name_err:
        return terminal_error("invalid_inputs", name_err)

    meta_path = Path(TEMPLATES_DIR) / f"{name}.meta.json"
    if not meta_path.is_file():
        return terminal_error("invalid_inputs", f"Template '{name}' not found")

    if workflow_json is None and metadata is None:
        return terminal_error("invalid_inputs",
            "At least one of workflow_json or metadata must be provided")

    if workflow_json is not None:
        if not isinstance(workflow_json, dict):
            return terminal_error("invalid_inputs", "workflow_json must be a JSON object")
        workflow_path = Path(TEMPLATES_DIR) / f"{name}.json"
        try:
            workflow_path.write_text(json.dumps(workflow_json, indent=2), encoding="utf-8")
        except OSError as exc:
            return transient_error("storage_error",
                f"Failed to write workflow for '{name}': {exc}")

    if metadata is not None:
        metadata["name"] = name
        meta_err = _validate_metadata(metadata)
        if meta_err:
            return terminal_error("invalid_inputs", meta_err)
        try:
            meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        except OSError as exc:
            return transient_error("storage_error",
                f"Failed to write metadata for '{name}': {exc}")

    logger.info("Template updated: %s", name)
    return {"status": "success", "name": name, "message": f"Template '{name}' updated"}
```

**MCP tool registrations in `server.py`:**

```python
@mcp.tool()
async def add_template(name: str, workflow_json: dict, metadata: dict) -> dict:
    """Add a new workflow template from an exported ComfyUI workflow.

    Saves the workflow JSON and metadata sidecar to the templates directory
    after validating the metadata structure. The template is immediately
    available for use with queue_prompt.

    The metadata must include: model (string), description (string), and
    inputs (object mapping input names to {node_id, field} definitions).
    Optional: aspect_ratios, resolution_nodes, expected_duration.

    Use this when the user has exported a workflow from ComfyUI's browser UI
    and wants to register it as a reusable template. Template names cannot
    contain path characters (/, ..) or start with a dot.
    """
    return await templates.add_template(name, workflow_json, metadata)


@mcp.tool()
async def update_template(
    name: str, workflow_json: dict | None = None, metadata: dict | None = None
) -> dict:
    """Update an existing workflow template's workflow JSON and/or metadata.

    Overwrites the specified files for an existing template. Provide
    workflow_json to update the workflow, metadata to update the sidecar,
    or both. At least one must be provided. Metadata is validated on write.

    Use this when a template needs to be updated after ComfyUI custom nodes
    change, or to refine template metadata (descriptions, input definitions,
    aspect ratios).
    """
    return await templates.update_template(name, workflow_json, metadata)
```

### Previous Story Intelligence

**Epic 2 Retrospective (2026-03-30) key takeaways:**
- Commit between stories -- create git commit after each story review before starting next
- Error handling is a systematic blind spot -- use the mental checklist: JSONDecodeError, OSError, RequestError subclasses, basename edge cases
- Detailed story specs enable single-pass implementation (Stories 2.3/2.4 had zero debug cycles)
- Code review catches real issues -- maintain high test coverage

**Story 2.4 established:**
- Pattern for file I/O with `OSError` catch -> `transient_error("storage_error", ...)`
- Pattern for `os.path.basename()` sanitization (we use `_validate_template_name` instead for write paths)
- 130 tests passing, zero regressions

**Story 2.1 established:**
- `comfyclaude/templates.py` with `list_templates()`, `get_template()` -- extend with `add_template()`, `update_template()`
- `_write_meta()` test helper in `test_templates.py` -- reuse for setup in new tests
- Template meta structure: `name`, `model`, `description`, `inputs`, `aspect_ratios`, `resolution_nodes`, `expected_duration`
- `templates_dir` fixture with `monkeypatch.setenv` + `importlib.reload` pattern

**Defensive hardening spec (2026-03-30) already resolved:**
- Config validation (empty env vars, trailing slashes, URL scheme) -- done
- Injection guards (`_inject_inputs`, `_inject_resolution` KeyError prevention) -- done
- Filename collision avoidance -- done

### Deferred Work Resolved by This Story

Per `deferred-work.md` and Epic 2 Retrospective:

| Deferred Item | Source | Resolution in This Story |
|---|---|---|
| Path traversal in `get_template` | 2.1 | `_validate_template_name` applied to `get_template` |
| Path traversal in `comfyui.py` template loading | 2.2 | Addressed by validation at the `templates.py` boundary before reaching `comfyui.py` |
| Symlinks followed without guard | 2.1 | Template name validation prevents escape from TEMPLATES_DIR; symlink following within the directory is accepted for MVP |

**Important:** When adding `_validate_template_name` to `get_template`, add it as the first check before `meta_path.is_file()`. This closes the deferred path traversal item without changing the function signature.

### Git Intelligence

**Recent commits:**
1. `6c51759` -- fix: defensive hardening (config validation, injection guards, filename collision)
2. `8fe95bd` -- Stories 2-3, 2-4 completion
3. `ab629a9` -- Epic 1-2 complete
4. `a1a2d24` -- Initial commit

**Patterns to follow:**
- UTF-8 encoding on all file reads/writes
- `json.dumps(data, indent=2)` for human-readable JSON output
- `OSError` as the catch-all for filesystem errors
- `transient_error("storage_error", ...)` for write failures
- `terminal_error("invalid_inputs", ...)` for validation failures

### Library & Framework Requirements

| Package | Version (installed) | Usage in this story |
|---------|-------------------|---------------------|
| fastmcp | 3.1.1 | `@mcp.tool()` decorator for add_template, update_template |
| pytest | 9.0.2 | Test framework |

**No new dependencies needed.** All functionality uses stdlib (`json`, `pathlib`, `logging`) and existing imports.

### File Structure Requirements

**Modify:**
```
comfyclaude/templates.py    # Add _validate_template_name, _validate_metadata, add_template, update_template; add validation to get_template
comfyclaude/server.py       # Add @mcp.tool() for add_template, update_template
tests/test_templates.py     # Add ~20 tests for CRUD + validation
```

**Do NOT create or modify:**
- `comfyclaude/comfyui.py` -- Not relevant to template management
- `comfyclaude/config.py` -- No changes needed
- `comfyclaude/errors.py` -- No changes needed
- `comfyclaude/init.py` -- Story 4.1
- `templates/` -- Do not modify starter templates
- `main.py` -- No changes needed
- `tests/conftest.py` -- Existing fixtures are sufficient
- `tests/test_comfyui.py` -- Not relevant

### Testing Requirements

Extend `tests/test_templates.py` using the existing `templates_dir` fixture and `_write_meta` helper.

**Sample workflow JSON for tests:**
```python
SAMPLE_WORKFLOW = {
    "6": {"inputs": {"text": ""}, "class_type": "CLIPTextEncode"},
    "47": {"inputs": {"width": 1024, "height": 1024}, "class_type": "EmptyLatentImage"},
}
```

**Sample metadata for tests:**
```python
SAMPLE_METADATA = {
    "name": "test_template",
    "model": "test-model",
    "description": "A test template",
    "inputs": {
        "prompt": {"node_id": "6", "field": "text", "type": "required", "description": "Text prompt"}
    },
    "aspect_ratios": {"1:1": {"width": 1024, "height": 1024}},
    "resolution_nodes": [{"node_id": "47", "width_field": "width", "height_field": "height"}],
    "expected_duration": "10 seconds",
}
```

**Testing patterns:**
- Use `templates_dir` fixture for isolated filesystem
- `_write_meta()` + manual `.json` write for pre-existing templates in update tests
- Assert file contents match by reading back and parsing JSON
- Assert `status`, `error_type`, `retry_suggested` fields on error responses
- For storage errors: use `monkeypatch` on `Path.write_text` to raise `OSError`

**Path traversal test names to include:**
```python
@pytest.mark.parametrize("bad_name", [
    "../evil",
    "foo/bar",
    ".hidden",
    "a..b",
    "",
    "   ",
])
async def test_add_template_rejects_bad_names(templates_dir, bad_name):
    ...
```

### Anti-Pattern Prevention

- Do NOT validate workflow JSON structure beyond "is it a dict" -- architecture decision: meta-only validation for MVP. Cross-reference validation (checking node_ids exist in workflow) is Phase 2.
- Do NOT construct error dicts manually -- use `terminal_error()` / `transient_error()` from `comfyclaude.errors`.
- Do NOT add `delete_template` in this story -- that is Story 3.2.
- Do NOT log with `print()` -- use `logging.getLogger(__name__)`.
- Do NOT import from `comfyclaude.comfyui` in `templates.py` -- these modules do not cross-import per architecture.
- Do NOT modify existing `list_templates` behavior -- only add the name validation to `get_template`.
- Do NOT add runtime type validation to `errors.py` -- taxonomy enforced by convention per architecture.
- Do NOT add async file I/O (aiofiles) -- synchronous I/O per architecture decision.
- Do NOT create a persistent httpx client -- no HTTP calls in `templates.py`.
- Do NOT add caching -- per architecture (NFR4: no caching required).

### Project Structure Notes

- `comfyclaude/templates.py` grows with `add_template()`, `update_template()`, `_validate_template_name()`, `_validate_metadata()` -- this is expected per architecture (Template Management FR4-8 maps to `templates.py`)
- Existing `get_template` gets path traversal protection added -- this is a backfill from deferred work, not a behavior change for valid inputs
- Tests extend `tests/test_templates.py` -- keeps all template tests together
- No new files are created in this story

### References

- [Source: _bmad-output/planning-artifacts/architecture.md#Template Filesystem Boundary] -- templates.py owns all template file operations
- [Source: _bmad-output/planning-artifacts/architecture.md#Requirements to Structure Mapping] -- Template Management (FR4-8) -> templates.py
- [Source: _bmad-output/planning-artifacts/architecture.md#Template Validation] -- Meta-only validation for MVP
- [Source: _bmad-output/planning-artifacts/architecture.md#Tool Return Format] -- dict with status field
- [Source: _bmad-output/planning-artifacts/architecture.md#Error Construction Pattern] -- transient_error/terminal_error usage
- [Source: _bmad-output/planning-artifacts/architecture.md#Tool Docstring Pattern] -- Rich docstrings for Claude Code
- [Source: _bmad-output/planning-artifacts/architecture.md#Code Naming Conventions] -- PEP 8 throughout
- [Source: _bmad-output/planning-artifacts/architecture.md#Test Organization] -- tests/ directory, test_ prefix
- [Source: _bmad-output/planning-artifacts/epics.md#Story 3.1] -- Acceptance criteria source
- [Source: _bmad-output/planning-artifacts/prd.md#FR4] -- Add workflow template
- [Source: _bmad-output/planning-artifacts/prd.md#FR5] -- Update existing template
- [Source: _bmad-output/planning-artifacts/prd.md#FR7] -- Schema validation on write
- [Source: _bmad-output/planning-artifacts/prd.md#FR8] -- Path traversal rejection
- [Source: _bmad-output/implementation-artifacts/2-4-image-retrieval-and-output-organization.md] -- Previous story OSError/storage_error patterns
- [Source: _bmad-output/implementation-artifacts/epic-2-retro-2026-03-30.md] -- Retro action items
- [Source: _bmad-output/implementation-artifacts/deferred-work.md] -- Path traversal items resolved by this story
- [Source: templates/flux2_klein.meta.json] -- Reference meta structure

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

No debug cycles needed — single-pass implementation.

### Completion Notes List

- Implemented `_validate_template_name()` for path traversal protection (FR8), applied to `get_template` (backfill), `add_template`, and `update_template`
- Implemented `_validate_metadata()` for meta structure validation (FR7) — validates required fields, inputs, aspect_ratios, resolution_nodes
- Implemented `add_template()` — validates name, workflow, metadata; checks for duplicates; writes both files atomically with cleanup on failure
- Implemented `update_template()` — validates name, checks existence; supports partial updates (workflow only, metadata only, or both)
- Registered both as MCP tools in `server.py` with rich docstrings
- 25 new tests covering all acceptance criteria; 178 total tests pass with zero regressions
- Resolved deferred path traversal items from Story 2.1

### Change Log

- 2026-03-30: Story 3.1 implementation — add_template, update_template, validation functions, path traversal protection, 25 new tests

### File List

- comfyclaude/templates.py (modified: added _validate_template_name, _validate_metadata, add_template, update_template; added validation to get_template; added transient_error import)
- comfyclaude/server.py (modified: registered add_template and update_template MCP tools)
- tests/test_templates.py (modified: added 25 new tests for add/update/validation/path traversal)
