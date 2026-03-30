# Story 3.2: Delete Workflow Templates

Status: done

## Story

As a developer using Claude Code,
I want to remove workflow templates I no longer need,
So that my template list stays clean and Claude Code doesn't suggest outdated workflows.

## Acceptance Criteria

1. **Given** an existing template named `sdxl_turbo`
   **When** I call the `delete_template` MCP tool with `name: "sdxl_turbo"`
   **Then** both `sdxl_turbo.json` and `sdxl_turbo.meta.json` are removed from the templates directory (FR6)

2. **Given** a template name that doesn't exist
   **When** I call `delete_template` with that name
   **Then** it returns a terminal error with `error_type: "invalid_inputs"` and a clear message

3. **Given** a template name containing path traversal characters
   **When** I call `delete_template` with that name
   **Then** it returns a terminal error with `error_type: "invalid_inputs"` (FR8)

4. **Given** a template was just deleted
   **When** I call `list_templates`
   **Then** the deleted template no longer appears

5. **Given** the story is complete
   **When** I run `uv run pytest tests/test_templates.py`
   **Then** all delete tests pass

## Tasks / Subtasks

- [x] Task 1: Implement `delete_template` in `comfyclaude/templates.py` (AC: #1, #2, #3, #4)
  - [x] 1.1 Implement `async def delete_template(name: str) -> dict`
  - [x] 1.2 Validate name with `_validate_template_name`; return `terminal_error("invalid_inputs", ...)` on failure
  - [x] 1.3 Check `.meta.json` exists; return `terminal_error("invalid_inputs", f"Template '{name}' not found")` if not
  - [x] 1.4 Delete `{name}.meta.json` first, then `{name}.json` (meta is the canonical existence marker)
  - [x] 1.5 Use `missing_ok=True` on `.json` deletion — handles the broken-template state where meta exists but workflow file is missing (deferred item from 3.1)
  - [x] 1.6 Catch `OSError` on deletion -> `transient_error("storage_error", f"Failed to delete template '{name}': {exc}")`
  - [x] 1.7 Log deletion: `logger.info("Template deleted: %s", name)`
  - [x] 1.8 Return `{"status": "success", "name": name, "message": "Template '{name}' deleted"}`

- [x] Task 2: Register MCP tool in `comfyclaude/server.py` (AC: #1)
  - [x] 2.1 Add `@mcp.tool()` for `delete_template` with rich docstring
  - [x] 2.2 Delegate to `templates.delete_template(name)`

- [x] Task 3: Write tests in `tests/test_templates.py` (AC: #5)
  - [x] 3.1 Test `delete_template` removes both `.json` and `.meta.json` files
  - [x] 3.2 Test `delete_template` returns success with name and message
  - [x] 3.3 Test `delete_template` for nonexistent template returns `invalid_inputs` error
  - [x] 3.4 Test `delete_template` with path traversal names (`../evil`, `foo/bar`, `.hidden`, `a..b`, `""`, `"   "`) returns `invalid_inputs` error
  - [x] 3.5 Test `delete_template` then `list_templates` no longer shows deleted template
  - [x] 3.6 Test `delete_template` when only `.meta.json` exists (no `.json` file) still succeeds
  - [x] 3.7 Test `delete_template` with storage error returns `storage_error` transient error
  - [x] 3.8 Test `delete_template` MCP tool is registered
  - [x] 3.9 Test `delete_template` with `None` or non-string name returns `invalid_inputs` error

## Dev Notes

### Architecture Compliance

**Module boundaries** -- Per architecture, `comfyclaude/templates.py` is the **only** module that reads/writes template files. `delete_template` belongs here alongside `list_templates`, `get_template`, `add_template`, `update_template`. `server.py` registers the MCP tool and delegates.

**Tool return format** -- All MCP tools return dicts with a `status` field:
- Success: `{"status": "success", "name": "<template_name>", "message": "..."}`
- Error: Use `terminal_error()` or `transient_error()` from `comfyclaude.errors`

**Path sanitization** -- FR8 requires rejecting template names with `/`, `..`, or leading `.`. Reuse `_validate_template_name()` already implemented in Story 3.1.

### Technical Requirements

**Deletion order matters:** Delete `.meta.json` first because it is the canonical existence marker used by `list_templates` (which globs `*.meta.json`), `get_template`, and `update_template` (which checks `.meta.json` via `is_file()`). Deleting meta first ensures the template immediately disappears from all queries even if the `.json` deletion fails.

**Handle broken-template state:** A template may have `.meta.json` but no `.json` file (deferred item from Story 3.1 review). Use `Path.unlink(missing_ok=True)` for the `.json` file so deletion succeeds in this case.

**Implementation:**

```python
async def delete_template(name: str) -> dict:
    """Delete a workflow template by name."""
    name_err = _validate_template_name(name)
    if name_err:
        return terminal_error("invalid_inputs", name_err)

    templates_path = Path(TEMPLATES_DIR)
    meta_path = templates_path / f"{name}.meta.json"
    workflow_path = templates_path / f"{name}.json"

    if not meta_path.is_file():
        return terminal_error("invalid_inputs", f"Template '{name}' not found")

    try:
        meta_path.unlink()
        workflow_path.unlink(missing_ok=True)
    except OSError as exc:
        return transient_error("storage_error", f"Failed to delete template '{name}': {exc}")

    logger.info("Template deleted: %s", name)
    return {"status": "success", "name": name, "message": f"Template '{name}' deleted"}
```

**MCP tool registration in `server.py`:**

```python
@mcp.tool()
async def delete_template(name: str) -> dict:
    """Delete a workflow template by name.

    Removes both the workflow JSON and metadata sidecar files from the
    templates directory. The template is immediately unavailable for use
    with queue_prompt.

    Use this when a template is outdated, broken, or no longer needed.
    Template names cannot contain path characters (/, ..) or start with
    a dot.
    """
    return await templates.delete_template(name)
```

### Previous Story Intelligence

**Story 3.1 established:**
- `_validate_template_name()` for path traversal protection -- reuse directly
- `_validate_metadata()` -- not needed for delete
- Pattern: validate name first, check existence, perform operation, catch `OSError`
- `_sample_meta()` and `_write_meta()` test helpers -- reuse for setup
- `SAMPLE_WORKFLOW` constant -- reuse for pre-populating templates in tests
- 178 tests passing, zero regressions

**Story 3.1 review findings (relevant to delete):**
- `_validate_template_name` already handles `None` and non-string via `isinstance` check (patched in 3.1)
- `OSError` catch on `unlink` should use the same `transient_error("storage_error", ...)` pattern
- `missing_ok=True` is safe for cleanup -- same approach used in `add_template` failure path

**Epic 2 Retrospective takeaways:**
- Error handling checklist: OSError is the key one for filesystem operations
- Detailed story specs enable single-pass implementation
- Commit between stories

### Git Intelligence

**Recent commits:**
1. `229285d` -- Story 3-1 complete: add/update workflow templates with validation
2. `6c51759` -- fix: defensive hardening
3. `8fe95bd` -- Stories 2-3, 2-4 completion
4. `ab629a9` -- Epic 1-2 complete
5. `a1a2d24` -- Initial commit

**Patterns to follow:**
- `Path.unlink(missing_ok=True)` for safe file removal (used in `add_template` cleanup)
- `terminal_error("invalid_inputs", ...)` for validation failures
- `transient_error("storage_error", ...)` for filesystem errors
- Test parametrize with `["../evil", "foo/bar", ".hidden", "a..b", "", "   "]` for bad names

### Library & Framework Requirements

| Package | Version (installed) | Usage in this story |
|---------|-------------------|---------------------|
| fastmcp | 3.1.1 | `@mcp.tool()` decorator for delete_template |
| pytest | 9.0.2 | Test framework |

**No new dependencies needed.** All functionality uses stdlib (`pathlib`, `logging`) and existing imports.

### File Structure Requirements

**Modify:**
```
comfyclaude/templates.py    # Add delete_template function
comfyclaude/server.py       # Add @mcp.tool() for delete_template
tests/test_templates.py     # Add ~9 tests for delete
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

Extend `tests/test_templates.py` using the existing `templates_dir` fixture, `_write_meta` helper, and `SAMPLE_WORKFLOW` constant.

**Test setup pattern for delete tests:**
```python
# Pre-populate a template for deletion
_write_meta(templates_dir, "to_delete")
(templates_dir / "to_delete.json").write_text(json.dumps(SAMPLE_WORKFLOW))
```

**Path traversal test pattern (reuse from add/update):**
```python
@pytest.mark.parametrize("bad_name", ["../evil", "foo/bar", ".hidden", "a..b", "", "   "])
async def test_delete_template_rejects_bad_names(templates_dir, bad_name):
    ...
```

**Storage error test pattern:**
```python
# Monkeypatch Path.unlink to raise OSError
original_unlink = Path.unlink

def failing_unlink(self, *args, **kwargs):
    if self.suffix == ".json":
        raise OSError("permission denied")
    return original_unlink(self, *args, **kwargs)

monkeypatch.setattr(Path, "unlink", failing_unlink)
```

**Testing assertions:**
- Assert file contents removed by checking `not path.exists()`
- Assert `status`, `error_type`, `retry_suggested` fields on error responses
- Assert `"delete_template"` in MCP tool names list

### Anti-Pattern Prevention

- Do NOT construct error dicts manually -- use `terminal_error()` / `transient_error()` from `comfyclaude.errors`.
- Do NOT add any new validation functions -- reuse `_validate_template_name()` from Story 3.1.
- Do NOT log with `print()` -- use `logging.getLogger(__name__)`.
- Do NOT import from `comfyclaude.comfyui` in `templates.py` -- these modules do not cross-import per architecture.
- Do NOT add async file I/O (aiofiles) -- synchronous I/O per architecture decision.
- Do NOT add confirmation prompts or "are you sure" logic -- MCP tools are atomic operations called by Claude Code.
- Do NOT delete only one file (`.json` or `.meta.json`) -- always attempt both. Use `missing_ok=True` for `.json` to handle broken-template state.
- Do NOT check for `.json` existence to determine template existence -- `.meta.json` is the canonical marker (consistent with `get_template` and `update_template`).

### Project Structure Notes

- `comfyclaude/templates.py` gains `delete_template()` -- completes the CRUD set (list, get, add, update, delete) per architecture (Template Management FR4-8 maps to `templates.py`)
- This is the final story in Epic 3 -- after this, all template management FRs (FR4-FR8) are fully implemented
- No new files are created in this story

### References

- [Source: _bmad-output/planning-artifacts/architecture.md#Template Filesystem Boundary] -- templates.py owns all template file operations
- [Source: _bmad-output/planning-artifacts/architecture.md#Requirements to Structure Mapping] -- Template Management (FR4-8) -> templates.py
- [Source: _bmad-output/planning-artifacts/architecture.md#Tool Return Format] -- dict with status field
- [Source: _bmad-output/planning-artifacts/architecture.md#Error Construction Pattern] -- transient_error/terminal_error usage
- [Source: _bmad-output/planning-artifacts/architecture.md#Tool Docstring Pattern] -- Rich docstrings for Claude Code
- [Source: _bmad-output/planning-artifacts/epics.md#Story 3.2] -- Acceptance criteria source
- [Source: _bmad-output/planning-artifacts/prd.md#FR6] -- Delete template by name
- [Source: _bmad-output/planning-artifacts/prd.md#FR8] -- Path traversal rejection
- [Source: _bmad-output/implementation-artifacts/3-1-add-and-update-workflow-templates-with-validation.md] -- Previous story patterns, validation functions, review findings
- [Source: _bmad-output/implementation-artifacts/deferred-work.md] -- Broken-template state (meta exists, .json missing) relevant to delete behavior

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

None — single-pass implementation, no issues encountered.

### Completion Notes List

- Implemented `delete_template()` in `comfyclaude/templates.py` following the exact pattern from Dev Notes: validate name, check meta existence, delete meta first then workflow with `missing_ok=True`, catch OSError, log and return success dict.
- Registered `delete_template` MCP tool in `server.py` with rich docstring consistent with existing tools.
- Added 16 new tests (9 test functions, some parametrized) covering all ACs: happy path deletion, success response format, nonexistent template error, path traversal rejection (6 bad names), delete-then-list exclusion, broken-template state (meta-only), storage error handling, MCP tool registration, and non-string name rejection.
- All 197 tests pass (16 new + 181 existing), zero regressions.
- Completes the CRUD set for template management (list, get, add, update, delete) — all Epic 3 FRs (FR4-FR8) now implemented.

### Change Log

- 2026-03-30: Story 3.2 implemented — delete_template function, MCP tool registration, 16 tests added

### File List

- comfyclaude/templates.py (modified — added `delete_template` function)
- comfyclaude/server.py (modified — added `@mcp.tool()` for `delete_template`)
- tests/test_templates.py (modified — added 16 delete template tests)

### Review Findings

- [x] [Review][Patch] Partial delete: `meta_path.unlink()` succeeds but `workflow_path.unlink()` OSError leaves orphaned `.json` with no cleanup path [slop_studio/templates.py:161-165]
- [x] [Review][Patch] Test gap: `test_delete_template_non_string_name` missing `retry_suggested is False` assertion [tests/test_templates.py:503-510]
- [x] [Review][Defer] TOCTOU race between `is_file()` check and `meta_path.unlink()` — concurrent delete returns misleading retryable storage_error [slop_studio/templates.py] — deferred, pre-existing pattern
- [x] [Review][Defer] Null bytes in name bypass `_validate_template_name` and raise `ValueError` uncaught by `OSError` handler [slop_studio/templates.py] — deferred, pre-existing
- [x] [Review][Defer] `_validate_template_name` does not reject backslash — POSIX-safe, pre-existing gap [slop_studio/templates.py] — deferred, pre-existing
- [x] [Review][Defer] `TEMPLATES_DIR` misconfiguration (not a directory) produces opaque error on delete — pre-existing pattern [slop_studio/templates.py] — deferred, pre-existing
- [x] [Review][Defer] No resolved-path confinement check (`meta_path.resolve().is_relative_to(templates_path.resolve())`) — blacklist approach relies on complete character enumeration; a future validator change could miss a bypass [slop_studio/templates.py] — deferred, pre-existing pattern across module
