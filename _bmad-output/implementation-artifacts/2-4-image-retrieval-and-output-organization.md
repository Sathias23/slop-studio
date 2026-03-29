# Story 2.4: Image Retrieval & Output Organization

Status: review

## Story

As a developer using Claude Code,
I want to retrieve the file path of my generated image,
So that I can view, use, or reference it directly from the terminal.

## Acceptance Criteria

1. **Given** a completed job with a `prompt_id`
   **When** I call the `get_image` MCP tool with `prompt_id`
   **Then** it returns the absolute file path to the output image (FR19)

2. **Given** the `get_image` tool retrieves an image from ComfyUI
   **When** the server saves the image
   **Then** it organizes it under `{output_dir}/{YYYY-MM-DD}/{filename}` (FR20)

3. **Given** the output date directory does not yet exist
   **When** the server saves an image
   **Then** it creates the date directory automatically

4. **Given** any image filename from ComfyUI
   **When** the server processes the filename
   **Then** it sanitizes it via `os.path.basename()` before any file operations to prevent path traversal (FR21)

5. **Given** a `prompt_id` for a job that hasn't completed
   **When** I call `get_image`
   **Then** it returns an appropriate error indicating the job isn't complete yet

6. **Given** a `prompt_id` for a completed job that produced no output
   **When** I call `get_image`
   **Then** it returns a terminal error with `error_type: "completed_no_output"` (FR24)

7. **Given** the output directory is not writable
   **When** the server attempts to save an image
   **Then** it returns a transient error with `error_type: "storage_error"` (FR23)

8. **Given** the story is complete
   **When** I run `uv run pytest tests/test_comfyui.py`
   **Then** all image retrieval and output organization tests pass

## Tasks / Subtasks

- [x] Task 1: Implement `get_image` in `comfyclaude/comfyui.py` (AC: #1, #2, #3, #4, #5, #6, #7)
  - [x] 1.1 Add `import os` and `from datetime import date` imports to `comfyclaude/comfyui.py`
  - [x] 1.2 Add `from comfyclaude.config import OUTPUT_DIR` to the existing config import line
  - [x] 1.3 Implement `async def get_image(prompt_id: str) -> dict`
  - [x] 1.4 Fetch job status via `_fetch_job_status(prompt_id)` to determine completion
  - [x] 1.5 Handle non-completed states: return error for pending/running jobs, propagate error for failed jobs
  - [x] 1.6 Extract first image filename from `outputs` dict (navigate output node data -> images array -> first entry's "filename")
  - [x] 1.7 Handle `completed_no_output`: no output nodes, empty images array, or missing filename
  - [x] 1.8 Sanitize filename with `os.path.basename()` (FR21)
  - [x] 1.9 Fetch image bytes from ComfyUI's `GET /view?filename={filename}&type=output` endpoint
  - [x] 1.10 Build date-organized output path: `{OUTPUT_DIR}/{YYYY-MM-DD}/{sanitized_filename}`
  - [x] 1.11 Create date directory with `os.makedirs(date_dir, exist_ok=True)`
  - [x] 1.12 Write image bytes to disk; catch `OSError` -> `transient_error("storage_error", ...)`
  - [x] 1.13 Return `{"status": "success", "file_path": "<absolute_path>", "prompt_id": "<prompt_id>"}`
  - [x] 1.14 Handle `httpx.ConnectError`/`httpx.TimeoutException` -> `transient_error("unreachable", ...)`
- [x] Task 2: Register `get_image` MCP tool in `server.py` (AC: #1)
  - [x] 2.1 Add `@mcp.tool()` for `get_image` with rich docstring explaining usage
  - [x] 2.2 Tool function delegates to `comfyui.get_image()`
- [x] Task 3: Write tests in `tests/test_comfyui.py` (AC: #8)
  - [x] 3.1 Test completed job returns absolute file path
  - [x] 3.2 Test image is saved under `{output_dir}/{YYYY-MM-DD}/{filename}`
  - [x] 3.3 Test date directory is created automatically
  - [x] 3.4 Test filename is sanitized with `os.path.basename()`
  - [x] 3.5 Test pending job returns error
  - [x] 3.6 Test running job returns error
  - [x] 3.7 Test completed job with no output returns `completed_no_output` error
  - [x] 3.8 Test storage error returns `storage_error` transient error
  - [x] 3.9 Test unreachable ComfyUI returns transient error
  - [x] 3.10 Test MCP `get_image` tool is registered in server
  - [x] 3.11 Test subfolder parameter is passed to ComfyUI `/view` when present

## Dev Notes

### Architecture Compliance

**Module boundaries** -- Per architecture, `comfyui.py` is the **only** module that makes HTTP calls to ComfyUI AND handles file operations for output images. The `get_image` function belongs in `comfyui.py` alongside `queue_prompt` and `check_job`. `server.py` registers the MCP tool and delegates to `comfyui.get_image()`.

**Tool return format** -- All MCP tools return dicts with a `status` field:
- Success: `{"status": "success", "file_path": "/absolute/path/to/image.png", "prompt_id": "..."}`
- Error: Use `transient_error()` or `terminal_error()` from `comfyclaude.errors`

**Async architecture** -- Use `httpx.AsyncClient` for the `/view` endpoint call. Create a new client per request with `timeout=30.0` (NFR1). File I/O (writing image bytes) is synchronous per architecture (NFR4: "synchronous file I/O for image operations").

### Technical Requirements

**ComfyUI `/view` API:**
- Endpoint: `GET {COMFYUI_URL}/view?filename={filename}&type=output&subfolder={subfolder}`
- Returns raw image bytes
- The `filename` and `subfolder` come from the job's outputs data structure (from `check_job`/`_fetch_job_status`)
- The `type` parameter should always be `output` for generated images

**Output data structure from ComfyUI history:**
```python
# From _fetch_job_status -> completed result outputs:
{
    "9": {
        "images": [
            {"filename": "ComfyUI_00042_.png", "subfolder": "", "type": "output"}
        ]
    }
}
```
The `get_image` function needs to:
1. Fetch job status to get the outputs dict
2. Walk the outputs dict to find the first node with an `images` array
3. Extract `filename` (and `subfolder` if non-empty) from the first image entry
4. Fetch the raw image bytes from `/view`
5. Save to the date-organized output directory

**Implementation approach:**

```python
import os
from datetime import date

async def get_image(prompt_id: str) -> dict:
    """Retrieve completed image, save to output directory, return absolute path."""
    # 1. Check job status
    try:
        result = await _fetch_job_status(prompt_id)
    except (httpx.ConnectError, httpx.TimeoutException):
        return transient_error("unreachable",
            f"Cannot connect to ComfyUI at {COMFYUI_URL}")
    except httpx.HTTPStatusError as exc:
        return transient_error("unreachable",
            f"ComfyUI returned HTTP {exc.response.status_code}")

    state = result["state"]

    if state == "pending":
        return terminal_error("invalid_inputs",
            f"Job {prompt_id} is still pending (queued, not started)")
    if state == "running":
        return terminal_error("invalid_inputs",
            f"Job {prompt_id} is still running. Call check_job with wait to poll for completion first.")
    if state == "failed":
        error_msg = result.get("error", "Job failed in ComfyUI")
        return terminal_error("generation_failed", error_msg)

    # state == "completed"
    outputs = result.get("outputs", {})

    # 2. Find first image in outputs
    filename = None
    subfolder = ""
    for node_output in outputs.values():
        images = node_output.get("images", [])
        if images:
            filename = images[0].get("filename")
            subfolder = images[0].get("subfolder", "")
            break

    if not filename:
        return terminal_error("completed_no_output",
            f"Job {prompt_id} completed but produced no output images")

    # 3. Sanitize filename (FR21)
    safe_filename = os.path.basename(filename)
    if not safe_filename:
        return terminal_error("completed_no_output",
            f"Job {prompt_id} produced an invalid filename")

    # 4. Fetch image bytes from ComfyUI
    params = {"filename": filename, "type": "output"}
    if subfolder:
        params["subfolder"] = subfolder

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{COMFYUI_URL}/view", params=params)
            response.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException):
        return transient_error("unreachable",
            f"Cannot connect to ComfyUI at {COMFYUI_URL}")
    except httpx.HTTPStatusError as exc:
        return transient_error("unreachable",
            f"ComfyUI returned HTTP {exc.response.status_code} fetching image")

    image_bytes = response.content

    # 5. Save to date-organized output directory
    date_str = date.today().isoformat()  # YYYY-MM-DD
    date_dir = os.path.join(OUTPUT_DIR, date_str)

    try:
        os.makedirs(date_dir, exist_ok=True)
    except OSError as exc:
        return transient_error("storage_error",
            f"Cannot create output directory '{date_dir}': {exc}")

    output_path = os.path.join(date_dir, safe_filename)
    try:
        with open(output_path, "wb") as f:
            f.write(image_bytes)
    except OSError as exc:
        return transient_error("storage_error",
            f"Cannot write image to '{output_path}': {exc}")

    abs_path = os.path.abspath(output_path)
    logger.info("Image saved: %s", abs_path)

    return {"status": "success", "file_path": abs_path, "prompt_id": prompt_id}
```

**Important implementation details:**

1. **Job status check reuses `_fetch_job_status()`** -- same internal helper from Story 2.3. No need for a separate HTTP call pattern.

2. **Pending/running jobs get `terminal_error("invalid_inputs")`** -- these are user errors (calling `get_image` too early). The error message tells Claude Code to call `check_job` first.

3. **Filename sanitization** -- `os.path.basename()` strips directory components. E.g., `"../../etc/passwd"` becomes `"passwd"`. Empty result after sanitization is treated as `completed_no_output`.

4. **Date directory format** -- `date.today().isoformat()` returns `YYYY-MM-DD` format per FR20.

5. **Synchronous file I/O** -- Per architecture (NFR4), standard synchronous file operations are used for writing image data. No need for async file I/O.

6. **`subfolder` parameter** -- ComfyUI's `/view` endpoint supports a `subfolder` parameter. Some workflows may place outputs in subfolders. Pass it through when non-empty.

7. **Error classification:**
   - `storage_error` (transient) for filesystem write failures -- the user might fix permissions and retry
   - `completed_no_output` (terminal) for jobs that completed without images -- retrying won't help
   - `unreachable` (transient) for ComfyUI connectivity issues
   - `invalid_inputs` (terminal) for calling get_image on non-completed jobs
   - `generation_failed` (terminal) for jobs that failed in ComfyUI

### MCP Tool Registration

**`server.py` addition:**

```python
@mcp.tool()
async def get_image(prompt_id: str) -> dict:
    """Retrieve the output image from a completed generation job.

    Downloads the image from ComfyUI, saves it to the output directory
    organized by date ({output_dir}/{YYYY-MM-DD}/{filename}), and returns
    the absolute file path.

    Call this after check_job returns status 'completed'. If the job is
    still running, call check_job with wait first to poll for completion.
    """
    return await comfyui.get_image(prompt_id)
```

### Previous Story Intelligence

**Story 2.3 established:**
- `comfyclaude/comfyui.py` with `_fetch_job_status()`, `_format_result()`, `check_job()` -- the `get_image` function reuses `_fetch_job_status()` to check completion before fetching
- `DEFAULT_POLL_INTERVAL` and `MAX_POLL_DURATION` constants -- not relevant to this story
- Existing imports: `asyncio`, `copy`, `json`, `logging`, `random`, `httpx`, `Path`, config imports, error imports -- add `os`, `date`
- `tests/test_comfyui.py` established patterns: `@pytest.mark.anyio` + `@respx.mock` decorators, monkeypatching `asyncio.sleep`
- History API response structure is already well-understood -- reuse the same mock patterns

**Story 2.2 established:**
- `queue_prompt()` returns `{"status": "success", "prompt_id": "..."}` -- `get_image` receives this prompt_id
- `_inject_inputs()`, `_randomize_seeds()`, `_inject_resolution()` -- not relevant to this story

**Story 2.1 established:**
- `comfyclaude/templates.py` -- not relevant
- `comfyclaude/server.py` already imports `comfyui` module -- no new import needed

**Story 1.1 established:**
- `comfyclaude/config.py` with `OUTPUT_DIR` constant -- `get_image` must import this
- `comfyclaude/errors.py` with `transient_error()` / `terminal_error()` helpers -- already imported in comfyui.py

**Epic 1 retrospective takeaways:**
- Detailed story specs enable single-pass implementation
- Code review catches real issues -- maintain high test coverage
- Import-time config constants require reload pattern in tests

### Library & Framework Requirements

| Package | Version (installed) | Usage in this story |
|---------|-------------------|---------------------|
| fastmcp | 3.1.1 | `@mcp.tool()` decorator for get_image registration |
| httpx | 0.28.1 | `AsyncClient` for `GET /view?filename=...&type=output` |
| respx | 0.22.0 | Mock httpx requests in tests |
| pytest | 9.0.2 | Test framework |

**No new dependencies needed.** New stdlib imports: `os` (for `os.path.basename`, `os.makedirs`, `os.path.join`, `os.path.abspath`), `datetime.date` (for `date.today().isoformat()`).

**Note:** `comfyclaude/comfyui.py` already imports `from pathlib import Path` but this story should use `os.path` for consistency with FR21's explicit `os.path.basename()` requirement and because the file I/O is straightforward path joining, not path object manipulation.

### File Structure Requirements

**Modify:**
```
comfyclaude/comfyui.py     # Add get_image(), add os/date imports, add OUTPUT_DIR to config import
comfyclaude/server.py      # Add @mcp.tool() for get_image
tests/test_comfyui.py      # Add image retrieval and output organization tests
```

**Do NOT create or modify:**
- `comfyclaude/templates.py` -- Not relevant to image retrieval
- `comfyclaude/config.py` -- No changes needed (OUTPUT_DIR already defined)
- `comfyclaude/errors.py` -- No changes needed
- `comfyclaude/init.py` -- Story 4.1
- `templates/` -- No template changes
- `main.py` -- No changes needed
- `tests/conftest.py` -- Existing fixtures are sufficient

### Testing Requirements

Add tests to `tests/test_comfyui.py` using `respx` for HTTP mocking, `tmp_path` for filesystem assertions, and `monkeypatch` for config overrides.

**ComfyUI response mocks:**

```python
# Completed job with image output
HISTORY_COMPLETED_WITH_IMAGE = {
    "abc-123": {
        "outputs": {
            "9": {"images": [{"filename": "ComfyUI_00042_.png", "subfolder": "", "type": "output"}]}
        },
        "status": {"status_str": "success", "completed": True, "messages": []}
    }
}

# Completed job with no outputs
HISTORY_COMPLETED_NO_OUTPUT = {
    "abc-123": {
        "outputs": {},
        "status": {"status_str": "success", "completed": True, "messages": []}
    }
}

# Image bytes (small valid PNG or any bytes)
FAKE_IMAGE_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
```

**Required test cases:**

- `test_get_image_completed_returns_file_path` -- Mock `/history/abc-123` returning completed with image, mock `/view` returning image bytes. Set `OUTPUT_DIR` to `tmp_path`. Assert response has `status: "success"` and `file_path` is an absolute path. Assert image file exists on disk.

- `test_get_image_saves_in_date_directory` -- Same setup as above. Assert file is saved at `{tmp_path}/{YYYY-MM-DD}/ComfyUI_00042_.png` where YYYY-MM-DD is today's date.

- `test_get_image_creates_date_directory` -- Set `OUTPUT_DIR` to a fresh tmp_path. Assert the date directory did not exist before, exists after.

- `test_get_image_sanitizes_filename` -- Mock history with filename `"../../etc/passwd"`. Mock `/view` returning bytes. Assert the saved file is `{date_dir}/passwd`, not a path traversal.

- `test_get_image_pending_returns_error` -- Mock empty history response (pending). Assert `terminal_error` with `error_type: "invalid_inputs"`.

- `test_get_image_running_returns_error` -- Mock running history response. Assert `terminal_error` with `error_type: "invalid_inputs"`.

- `test_get_image_completed_no_output_returns_error` -- Mock completed but empty outputs. Assert `terminal_error` with `error_type: "completed_no_output"`.

- `test_get_image_storage_error` -- Set `OUTPUT_DIR` to a non-writable path (or monkeypatch `os.makedirs` to raise `OSError`). Assert `transient_error` with `error_type: "storage_error"`.

- `test_get_image_unreachable_returns_transient_error` -- Mock `ConnectError` on `/history`. Assert `transient_error` with `error_type: "unreachable"`.

- `test_mcp_get_image_registered` -- Verify `get_image` appears in `mcp.list_tools()`.

- `test_get_image_passes_subfolder_to_view` -- Mock history with non-empty `subfolder: "subfolder_name"`. Assert `/view` was called with `subfolder=subfolder_name` query parameter.

**Testing pattern for OUTPUT_DIR override:**

```python
@pytest.fixture
def output_dir(tmp_path, monkeypatch):
    """Override OUTPUT_DIR to use tmp_path for test isolation."""
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(comfyclaude.comfyui, "OUTPUT_DIR", str(out))
    return out
```

**Note:** Since `comfyui.py` imports `OUTPUT_DIR` from config at module level, use `monkeypatch.setattr(comfyclaude.comfyui, "OUTPUT_DIR", ...)` to override it in the comfyui module's namespace. The `reload_config` autouse fixture in conftest.py will reset it after each test.

**Important:** The `comfyui.py` config import line currently reads `from comfyclaude.config import COMFYUI_URL, TEMPLATES_DIR`. Add `OUTPUT_DIR` to this import. Tests should then monkeypatch `comfyclaude.comfyui.OUTPUT_DIR` directly.

### Anti-Pattern Prevention

- Do NOT import from `comfyclaude.templates` in this story -- `get_image` has no template involvement.
- Do NOT create a persistent `httpx.AsyncClient` at module level -- create per-request. Persistent client is Phase 2.
- Do NOT add async file I/O (aiofiles) -- synchronous I/O per architecture decision (NFR4).
- Do NOT construct error dicts manually -- use `terminal_error()` / `transient_error()` from `comfyclaude.errors`.
- Do NOT log with `print()` -- use `logging.getLogger(__name__)`.
- Do NOT use the original unsanitized filename in any file path construction -- always apply `os.path.basename()` first.
- Do NOT add template management tools -- that is Epic 3.
- Do NOT modify `check_job`, `queue_prompt`, or any existing functions -- this story only adds `get_image`.
- Do NOT hard-code the output directory path -- use `OUTPUT_DIR` from config.
- Do NOT skip the job status check -- always verify the job is completed before fetching the image.
- Do NOT cache job status or image data -- per architecture (NFR4: no caching required).

### Deferred Work Awareness

From prior stories (tracked in `deferred-work.md`):
- **Uncaught `httpx.RequestError` subclasses** -- `ReadError`, `RemoteProtocolError`, etc. Same pattern applies to `get_image`'s `/view` call. Consistent with existing codebase approach (catch `ConnectError` and `TimeoutException` specifically).
- **Config import-time evaluation** -- Tests must monkeypatch `comfyclaude.comfyui.OUTPUT_DIR` since the module imports `OUTPUT_DIR` at load time.
- **`TEMPLATES_DIR`/`OUTPUT_DIR` relative path defaults** -- `OUTPUT_DIR` defaults to `./output` which is CWD-dependent. Accepted for MVP.

### Project Structure Notes

- `comfyclaude/comfyui.py` grows with `get_image()` -- this is expected per architecture (Image Retrieval FR19-21 maps to `comfyui.py`)
- Tests extend `tests/test_comfyui.py` -- this completes Epic 2's test coverage for `comfyui.py`
- No new files are created in this story
- This is the **final story in Epic 2** -- after this, the complete image generation end-to-end flow works

### References

- [Source: _bmad-output/planning-artifacts/architecture.md#Architectural Boundaries] -- ComfyUI HTTP Boundary + filesystem for output
- [Source: _bmad-output/planning-artifacts/architecture.md#Requirements to Structure Mapping] -- Image Retrieval (FR19-21) -> comfyui.py
- [Source: _bmad-output/planning-artifacts/architecture.md#Tool Return Format] -- dict with status field
- [Source: _bmad-output/planning-artifacts/architecture.md#Error Construction Pattern] -- transient_error/terminal_error usage
- [Source: _bmad-output/planning-artifacts/architecture.md#Tool Docstring Pattern] -- Rich docstrings for Claude Code
- [Source: _bmad-output/planning-artifacts/architecture.md#Test Organization] -- tests/ directory, respx for HTTP mocking
- [Source: _bmad-output/planning-artifacts/architecture.md#Async Architecture] -- async tools, synchronous file I/O
- [Source: _bmad-output/planning-artifacts/epics.md#Story 2.4] -- Acceptance criteria source
- [Source: _bmad-output/planning-artifacts/prd.md#FR19] -- Retrieve absolute image file path
- [Source: _bmad-output/planning-artifacts/prd.md#FR20] -- Date-organized output directory
- [Source: _bmad-output/planning-artifacts/prd.md#FR21] -- Filename sanitization via os.path.basename()
- [Source: _bmad-output/planning-artifacts/prd.md#FR23] -- storage_error transient classification
- [Source: _bmad-output/planning-artifacts/prd.md#FR24] -- completed_no_output terminal classification
- [Source: _bmad-output/planning-artifacts/prd.md#NFR1] -- 30-second HTTP timeout
- [Source: _bmad-output/planning-artifacts/prd.md#NFR4] -- Synchronous file I/O, no caching
- [Source: _bmad-output/implementation-artifacts/2-3-job-monitoring-and-completion-polling.md] -- Previous story context, _fetch_job_status reuse
- [Source: _bmad-output/implementation-artifacts/2-2-job-submission-with-input-injection-and-seed-randomization.md] -- queue_prompt patterns
- [Source: _bmad-output/implementation-artifacts/deferred-work.md] -- Known deferred items

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

None — single-pass implementation, all tests passed on first run.

### Completion Notes List

- Implemented `get_image()` in `comfyclaude/comfyui.py` following the story spec exactly: fetches job status via `_fetch_job_status()`, validates completion state, extracts first image filename from outputs, sanitizes with `os.path.basename()`, fetches raw image bytes from ComfyUI `/view` endpoint, saves to date-organized output directory `{OUTPUT_DIR}/{YYYY-MM-DD}/{filename}`, returns absolute file path.
- Error handling covers all specified cases: `invalid_inputs` (terminal) for pending/running jobs, `generation_failed` (terminal) for failed jobs, `completed_no_output` (terminal) for jobs with no images, `storage_error` (transient) for filesystem write failures, `unreachable` (transient) for ComfyUI connectivity issues.
- Registered `get_image` MCP tool in `server.py` with rich docstring for Claude Code.
- Added 11 new tests covering all acceptance criteria: file path return, date directory organization, directory auto-creation, filename sanitization, pending/running error, no-output error, storage error, unreachable error, MCP tool registration, subfolder parameter passthrough.
- All 130 tests pass (34 in test_comfyui.py), zero regressions.

### Change Log

- 2026-03-29: Implemented Story 2.4 — Image Retrieval & Output Organization. Added `get_image()` to `comfyclaude/comfyui.py`, registered MCP tool in `server.py`, added 11 tests to `tests/test_comfyui.py`.

### File List

- comfyclaude/comfyui.py (modified — added `get_image()`, `os`/`date`/`OUTPUT_DIR` imports)
- comfyclaude/server.py (modified — added `get_image` MCP tool registration)
- tests/test_comfyui.py (modified — added 11 image retrieval tests and `output_dir` fixture)
