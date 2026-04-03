---
title: 'Add image input support to template engine'
type: 'feature'
created: '2026-04-03'
status: 'done'
baseline_commit: '526996e'
context: ['_bmad-output/planning-artifacts/research/technical-flux2-klein-image-inputs-research-2026-04-03.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** slop-studio templates only support primitive value injection (text, numbers). The new `flux2_klein_edit` template requires image file inputs that must be uploaded to ComfyUI's `/upload/image` endpoint before the filename can be injected into `LoadImage` nodes.

**Approach:** Add an `_upload_image()` async helper to `comfyui.py` that uploads a local file to ComfyUI and returns the filename. Extend `_inject_inputs()` to detect `input_type: "image"` in meta.json definitions and upload-then-inject for those inputs. Add Pillow as a dependency for image validation.

## Boundaries & Constraints

**Always:**
- Existing text-to-image templates must work unchanged (backwards compatible)
- Image validation via Pillow `verify()` before upload (reject non-image files)
- UUID-prefix uploaded filenames to prevent collisions
- Use `httpx.AsyncClient` consistent with existing codebase
- `_inject_inputs()` becomes async; update its single callsite in `queue_prompt()`

**Ask First:**
- Changing the MCP tool signature for `queue_prompt` (we don't expect to — file paths come through the existing `inputs` dict)

**Never:**
- Base64 inline image encoding (too large for workflow JSON)
- New MCP tools for upload (keep it transparent inside `queue_prompt`)
- Modifying the existing `flux2_klein` text-to-image template

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path: image input | `inputs={"prompt": "edit text", "image": "/path/to/photo.png"}`, meta has `input_type: "image"` | Image uploaded to ComfyUI, workflow node gets filename, prompt queued | N/A |
| Non-image file | `inputs={"image": "/path/to/file.txt"}` | Rejected before upload | `terminal_error("validation", "File is not a valid image: ...")` |
| Missing file | `inputs={"image": "/nonexistent.png"}` | Rejected before upload | `terminal_error("validation", "Image file not found: ...")` |
| Upload fails (ComfyUI down) | Valid image but ComfyUI unreachable | Upload HTTP error | `transient_error("comfyui_unreachable", ...)` |
| No input_type field | Existing text template, no `input_type` in meta | Injected as string (unchanged behavior) | N/A |
| Optional image not provided | Meta has `type: "optional"` + `input_type: "image"`, user omits key | Skipped, no upload | N/A |

</frozen-after-approval>

## Code Map

- `slop_studio/comfyui.py` -- Core: `_inject_inputs()` (line 21), `queue_prompt()` (line 78, calls inject at 123)
- `tests/test_comfyui.py` -- 40 existing tests, pytest+respx+anyio pattern
- `pyproject.toml` -- Dependencies (Pillow not yet present)
- `templates/flux2_klein_edit.meta.json` -- Uses `input_type: "image"` (already created)

## Tasks & Acceptance

**Execution:**
- [x] `pyproject.toml` -- Add `Pillow>=11.0.0` to dependencies
- [x] `slop_studio/comfyui.py` -- Add `async def _upload_image(file_path: str) -> str` that validates with Pillow, uploads via POST `/upload/image`, returns ComfyUI filename. Make `_inject_inputs()` async, add `input_type: "image"` branch that calls `_upload_image()`. Update `queue_prompt()` line 123 to `await _inject_inputs(...)`.
- [x] `tests/test_comfyui.py` -- Add tests: happy path image upload+inject, non-image rejection, missing file rejection, upload failure, backwards compat with text-only template, optional image skipped.

**Acceptance Criteria:**
- Given a template with `input_type: "image"`, when `queue_prompt` is called with a valid image path, then the image is uploaded to `/upload/image` and the returned filename is injected into the workflow node
- Given a template without `input_type`, when `queue_prompt` is called, then behavior is identical to before (no upload, string injection)
- Given an invalid file (not an image or missing), when `queue_prompt` is called, then a `terminal_error` is returned without contacting ComfyUI

## Verification

**Commands:**
- `cd /Users/sathias/Projects/slop-studio && python -m pytest tests/test_comfyui.py -v` -- expected: all tests pass including new image input tests
- `cd /Users/sathias/Projects/slop-studio && pip install -e .` -- expected: Pillow installs successfully

## Suggested Review Order

**Image upload and injection pipeline**

- Entry point: new `_upload_image()` — validates with Pillow, uploads to ComfyUI, returns filename
  [`comfyui.py:22`](../../slop_studio/comfyui.py#L22)

- `_inject_inputs()` now async with `input_type: "image"` branch before injection
  [`comfyui.py:56`](../../slop_studio/comfyui.py#L56)

- Error handling in `queue_prompt()` — catches ValueError, RequestError, HTTPStatusError from inject
  [`comfyui.py:163`](../../slop_studio/comfyui.py#L163)

**Template definition**

- New edit template: flattened multi-reference Klein workflow (24 nodes)
  [`flux2_klein_edit.json:1`](../../templates/flux2_klein_edit.json#L1)

- Meta with `input_type: "image"` inputs for primary + reference image
  [`flux2_klein_edit.meta.json:1`](../../templates/flux2_klein_edit.meta.json#L1)

**Dependencies and tests**

- Pillow added to dependencies
  [`pyproject.toml:9`](../../pyproject.toml#L9)

- 6 new tests: happy path, non-image rejection, missing file, upload failure, backwards compat, optional skip
  [`test_comfyui.py:800`](../../tests/test_comfyui.py#L800)

- 3 existing tests updated to `async` to match `_inject_inputs` signature change
  [`test_comfyui.py:694`](../../tests/test_comfyui.py#L694)
