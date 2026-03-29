# Story 2.3: Job Monitoring & Completion Polling

Status: review

## Story

As a developer using Claude Code,
I want to poll a submitted job for completion,
So that I know when my image is ready without manually checking ComfyUI.

## Acceptance Criteria

1. **Given** a valid `prompt_id` from a submitted job
   **When** I call the `check_job` MCP tool with `prompt_id` and optional `wait` duration
   **Then** it returns structured status: `pending`, `running`, `completed`, or `failed` with error details (FR14, FR15)

2. **Given** a job is still processing and `wait` is specified (e.g., `wait: 30`)
   **When** the server polls ComfyUI's `/history/{id}` API
   **Then** it polls at 3-second intervals, capped at 45 seconds maximum (FR16)

3. **Given** a job completes before the timeout cap
   **When** the server is polling
   **Then** it returns immediately with `status: "completed"` and output details without waiting for the remaining timeout (FR17)

4. **Given** a `prompt_id` and `wait: 0` (or no wait parameter)
   **When** I call `check_job`
   **Then** it performs a single non-blocking status check and returns immediately (FR18)

5. **Given** the poll timeout is reached and the job is still running
   **When** the server returns
   **Then** it returns `status: "running"` so Claude Code knows to poll again

6. **Given** the job failed in ComfyUI
   **When** `check_job` detects the failure
   **Then** it returns `status: "failed"` with a descriptive error message and appropriate error type

7. **Given** the story is complete
   **When** I run `uv run pytest tests/test_comfyui.py`
   **Then** all polling and job monitoring tests pass with mocked HTTP

## Tasks / Subtasks

- [x] Task 1: Implement `check_job` in `comfyclaude/comfyui.py` (AC: #1, #2, #3, #4, #5, #6)
  - [x] 1.1 Add `async def check_job(prompt_id: str, wait: int = 0) -> dict` to `comfyclaude/comfyui.py`
  - [x] 1.2 Implement single non-blocking status check: `GET {COMFYUI_URL}/history/{prompt_id}`
  - [x] 1.3 Parse ComfyUI history response to determine job status (pending/running/completed/failed)
  - [x] 1.4 Implement polling loop: if `wait > 0`, poll at `DEFAULT_POLL_INTERVAL` (3s) intervals using `asyncio.sleep`
  - [x] 1.5 Cap total polling time at `MAX_POLL_DURATION` (45s), regardless of `wait` value
  - [x] 1.6 Return early on completion or failure (do not wait remaining timeout)
  - [x] 1.7 Return `{"status": "running"}` when poll timeout is reached and job is still in progress
  - [x] 1.8 Return `{"status": "completed", "prompt_id": ..., "outputs": ...}` on completion with output node data
  - [x] 1.9 Return `{"status": "failed", "error": ..., "error_type": ..., "retry_suggested": ...}` on failure using error helpers
  - [x] 1.10 Handle `httpx.ConnectError` / `httpx.TimeoutException` -> `transient_error("unreachable", ...)`
- [x] Task 2: Register `check_job` MCP tool in `server.py` (AC: #1)
  - [x] 2.1 Add `@mcp.tool()` for `check_job` with rich docstring explaining usage patterns
  - [x] 2.2 Tool function delegates to `comfyui.check_job()`
- [x] Task 3: Write tests in `tests/test_comfyui.py` (AC: #7)
  - [x] 3.1 Test single non-blocking status check (wait=0) returns immediate result
  - [x] 3.2 Test polling returns completed status with output details on success
  - [x] 3.3 Test early return when job completes before timeout
  - [x] 3.4 Test poll timeout returns running status
  - [x] 3.5 Test failed job returns error with descriptive message
  - [x] 3.6 Test pending job (not yet in history) returns pending status
  - [x] 3.7 Test unreachable ComfyUI during polling returns transient error
  - [x] 3.8 Test poll interval is 3 seconds (verify asyncio.sleep calls)
  - [x] 3.9 Test wait is capped at 45 seconds maximum
  - [x] 3.10 Test MCP check_job tool is registered in server

## Dev Notes

### Architecture Compliance

**Module boundaries** -- Per architecture, `comfyui.py` is the **only** module that makes HTTP calls to ComfyUI. The `check_job` function belongs in `comfyui.py` alongside `queue_prompt`. `server.py` registers the MCP tool and delegates to `comfyui.check_job()`.

**Tool return format** -- All MCP tools return dicts with a `status` field:
- Running: `{"status": "running", "prompt_id": "..."}`
- Completed: `{"status": "completed", "prompt_id": "...", "outputs": {...}}`
- Pending: `{"status": "pending", "prompt_id": "..."}`
- Failed: Use `transient_error()` or `terminal_error()` from `comfyclaude.errors`
- Connection error: Use `transient_error("unreachable", ...)`

**Note on status field:** The `check_job` tool uses descriptive status values (`pending`, `running`, `completed`, `failed`) rather than the generic `success`/`error` used by other tools. This is intentional -- the tool's purpose IS to report job status. Error conditions (unreachable ComfyUI, etc.) still use the standard error helpers which set `status: "error"`.

**Async architecture** -- Use `asyncio.sleep` for polling intervals. Create a new `httpx.AsyncClient` per request with `timeout=30.0` (NFR1). Do NOT reuse the lifespan client or create a persistent client -- that is Phase 2.

### Technical Requirements

**ComfyUI `/history/{prompt_id}` API:**
- Endpoint: `GET {COMFYUI_URL}/history/{prompt_id}`
- Returns empty dict `{}` when `prompt_id` is not yet in history (job is pending/queued)
- Returns `{prompt_id: {"outputs": {...}, "status": {...}}}` when job is in history
- The `status` object contains: `{"status_str": "success" | "error", "completed": true | false, "messages": [...]}`
- The `outputs` object maps node IDs to their output data (e.g., `{"9": {"images": [{"filename": "ComfyUI_00042_.png", "subfolder": "", "type": "output"}]}}`)

**Status determination logic:**

```python
import asyncio
import logging

import httpx

from comfyclaude.config import COMFYUI_URL
from comfyclaude.errors import transient_error, terminal_error

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 3  # seconds between polls (FR16)
MAX_POLL_DURATION = 45     # maximum total polling time in seconds (FR16)


async def _fetch_job_status(prompt_id: str) -> dict:
    """Fetch job status from ComfyUI history API.

    Returns:
        {"state": "pending"} if prompt_id not in history
        {"state": "running"} if job is in history but not completed
        {"state": "completed", "outputs": {...}} if job finished successfully
        {"state": "failed", "error": "..."} if job failed
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(f"{COMFYUI_URL}/history/{prompt_id}")
        response.raise_for_status()

    data = response.json()

    if prompt_id not in data:
        return {"state": "pending"}

    job_data = data[prompt_id]
    status_info = job_data.get("status", {})
    status_str = status_info.get("status_str", "")
    completed = status_info.get("completed", False)

    if status_str == "error":
        # Extract error message from status messages
        messages = status_info.get("messages", [])
        error_msg = "Job failed in ComfyUI"
        for msg in messages:
            if isinstance(msg, list) and len(msg) >= 2 and msg[0] == "execution_error":
                error_detail = msg[1] if isinstance(msg[1], str) else str(msg[1])
                error_msg = f"Job failed: {error_detail[:500]}"
                break
        return {"state": "failed", "error": error_msg}

    if completed and status_str == "success":
        outputs = job_data.get("outputs", {})
        return {"state": "completed", "outputs": outputs}

    return {"state": "running"}
```

**Full `check_job` function:**

```python
async def check_job(prompt_id: str, wait: int = 0) -> dict:
    """Check job status, optionally polling until completion or timeout."""
    effective_wait = min(wait, MAX_POLL_DURATION)  # Cap at 45s

    try:
        result = await _fetch_job_status(prompt_id)
    except (httpx.ConnectError, httpx.TimeoutException):
        return transient_error("unreachable",
            f"Cannot connect to ComfyUI at {COMFYUI_URL}")
    except httpx.HTTPStatusError as exc:
        return transient_error("unreachable",
            f"ComfyUI returned HTTP {exc.response.status_code}")

    # Non-blocking check or terminal state
    if effective_wait <= 0 or result["state"] in ("completed", "failed"):
        return _format_result(prompt_id, result)

    # Polling loop
    elapsed = 0.0
    while elapsed < effective_wait:
        await asyncio.sleep(DEFAULT_POLL_INTERVAL)
        elapsed += DEFAULT_POLL_INTERVAL

        try:
            result = await _fetch_job_status(prompt_id)
        except (httpx.ConnectError, httpx.TimeoutException):
            return transient_error("unreachable",
                f"Cannot connect to ComfyUI at {COMFYUI_URL}")
        except httpx.HTTPStatusError as exc:
            return transient_error("unreachable",
                f"ComfyUI returned HTTP {exc.response.status_code}")

        if result["state"] in ("completed", "failed"):
            return _format_result(prompt_id, result)

    # Timeout reached, still running/pending
    return _format_result(prompt_id, result)


def _format_result(prompt_id: str, result: dict) -> dict:
    """Convert internal status to MCP tool response format."""
    state = result["state"]

    if state == "completed":
        return {
            "status": "completed",
            "prompt_id": prompt_id,
            "outputs": result.get("outputs", {}),
        }

    if state == "failed":
        error_msg = result.get("error", "Job failed in ComfyUI")
        return terminal_error("generation_failed", error_msg)

    # pending or running
    return {"status": state, "prompt_id": prompt_id}
```

**Important implementation details:**

1. **`wait` parameter semantics:** `wait=0` (default) performs a single check and returns immediately. `wait=30` means "poll for up to 30 seconds." `wait` values above 45 are capped to 45 (FR16).

2. **Polling behavior:** The loop sleeps FIRST, then checks. This means the first poll happens at `DEFAULT_POLL_INTERVAL` seconds, not immediately (the immediate check already happened before the loop). This avoids a redundant first check.

3. **Error classification for failed jobs:** Use `terminal_error("generation_failed", ...)` for jobs that failed in ComfyUI. The error type `generation_failed` is a transient type per architecture (the user could fix their workflow and retry), but the job itself is terminal. Use `terminal_error` here because retrying the same prompt_id won't help -- the user needs to queue a new job.

4. **HTTPStatusError during polling:** Treat as transient (`unreachable`) since ComfyUI may temporarily return errors during heavy load. This differs from Story 2.2's `queue_prompt` which treats 4xx as `terminal_error("invalid_workflow")` -- the history endpoint should not return 4xx for valid prompt_ids, so any HTTP error during polling is likely a server issue.

### MCP Tool Registration

**`server.py` addition:**

```python
@mcp.tool()
async def check_job(prompt_id: str, wait: int = 0) -> dict:
    """Check the status of a submitted image generation job.

    Returns the current job status: pending (queued), running (processing),
    completed (with output details), or failed (with error message).

    By default performs a single non-blocking check. Set wait (in seconds)
    to poll until completion or timeout. Polls every 3 seconds, capped at
    45 seconds maximum.

    After queue_prompt returns a prompt_id, call this with wait=30 to poll
    for completion. If status is still 'running', call again. Once status
    is 'completed', call get_image to retrieve the output file path.
    """
    return await comfyui.check_job(prompt_id, wait)
```

### Previous Story Intelligence

**Story 2.2 established:**
- `comfyclaude/comfyui.py` with `queue_prompt()`, `_inject_inputs()`, `_randomize_seeds()`, `_inject_resolution()` -- the `check_job` function goes in the same module
- Imports already in place: `httpx`, `logging`, `from comfyclaude.config import COMFYUI_URL`, `from comfyclaude.errors import transient_error, terminal_error`
- New imports needed: `asyncio` (for `asyncio.sleep`)
- `tests/test_comfyui.py` has established patterns: `templates_dir` fixture, `COMFYUI_URL` constant, `@pytest.mark.anyio` + `@respx.mock` decorators
- Review findings from 2.2 (not yet resolved): 503/5xx routing, uncaught httpx.RequestError subclasses, KeyError on malformed responses, JSONDecodeError outside try/except. These are separate issues that do not block this story but inform defensive coding practices.

**Story 2.1 established:**
- `comfyclaude/templates.py` with template metadata functions (not relevant to this story)
- `comfyclaude/server.py` already imports `comfyui` module -- no new import needed

**Story 1.2 established:**
- `comfyclaude/server.py` with `mcp = FastMCP("comfyclaude", lifespan=lifespan)` -- add `@mcp.tool()` for `check_job`
- Lifespan client is separate from runtime clients

**Story 1.1 established:**
- `comfyclaude/config.py` with `COMFYUI_URL` constant
- `comfyclaude/errors.py` with `transient_error()` / `terminal_error()` helpers
- `tests/conftest.py` with autouse `reload_config` fixture

**Epic 1 retrospective takeaways:**
- Detailed story specs enable single-pass implementation
- Code review catches real issues -- maintain high test coverage
- Import-time config constants require reload pattern in tests

### Library & Framework Requirements

| Package | Version (installed) | Usage in this story |
|---------|-------------------|---------------------|
| fastmcp | 3.1.1 | `@mcp.tool()` decorator for check_job registration |
| httpx | 0.28.1 | `AsyncClient` for `GET /history/{prompt_id}` |
| respx | 0.22.0 | Mock httpx requests in tests |
| pytest | 9.0.2 | Test framework |

**No new dependencies needed.** Only new stdlib import: `asyncio` (for `asyncio.sleep` in polling loop).

### File Structure Requirements

**Modify:**
```
comfyclaude/comfyui.py     # Add check_job(), _fetch_job_status(), _format_result(), constants
comfyclaude/server.py      # Add @mcp.tool() for check_job
tests/test_comfyui.py      # Add polling and job monitoring tests
```

**Do NOT create or modify:**
- `comfyclaude/templates.py` -- Not relevant to job monitoring
- `comfyclaude/config.py` -- No changes needed (COMFYUI_URL already available)
- `comfyclaude/errors.py` -- No changes needed
- `comfyclaude/init.py` -- Story 4.1
- `templates/` -- No template changes
- `main.py` -- No changes needed
- `tests/conftest.py` -- Existing fixtures are sufficient

### Testing Requirements

Add tests to `tests/test_comfyui.py` using `respx` for HTTP mocking and `monkeypatch` for `asyncio.sleep`:

**ComfyUI history response mocks:**

```python
# Job completed successfully
HISTORY_COMPLETED = {
    "abc-123": {
        "outputs": {
            "9": {"images": [{"filename": "ComfyUI_00042_.png", "subfolder": "", "type": "output"}]}
        },
        "status": {"status_str": "success", "completed": True, "messages": []}
    }
}

# Job failed
HISTORY_FAILED = {
    "abc-123": {
        "outputs": {},
        "status": {
            "status_str": "error",
            "completed": True,
            "messages": [
                ["execution_error", {"message": "Node type 'KSamplerAdvanced_v2' not found", "node_id": "3"}]
            ]
        }
    }
}

# Job running (in history but not completed)
HISTORY_RUNNING = {
    "abc-123": {
        "outputs": {},
        "status": {"status_str": "", "completed": False, "messages": []}
    }
}

# Job pending (not in history yet) -- empty dict
HISTORY_PENDING = {}
```

**Required test cases:**

- `test_check_job_single_check_completed` -- Mock `/history/abc-123` returning completed, call `check_job("abc-123")` (wait=0), assert `{"status": "completed", "prompt_id": "abc-123", "outputs": {...}}`
- `test_check_job_single_check_pending` -- Mock empty history response, assert `{"status": "pending", "prompt_id": "abc-123"}`
- `test_check_job_single_check_running` -- Mock running response, assert `{"status": "running", "prompt_id": "abc-123"}`
- `test_check_job_polling_returns_completed` -- Mock first call as running, second call as completed. Monkeypatch `asyncio.sleep` to no-op. Call with `wait=30`. Assert completed result.
- `test_check_job_early_return_on_completion` -- Mock completed on first poll. Verify `asyncio.sleep` was called only once (3s), not the full wait duration.
- `test_check_job_poll_timeout_returns_running` -- Mock all calls as running. Monkeypatch `asyncio.sleep` to no-op. Call with `wait=10`. Assert `{"status": "running", ...}`.
- `test_check_job_failed_returns_error` -- Mock failed response. Assert terminal error with `error_type: "generation_failed"`.
- `test_check_job_unreachable_returns_transient_error` -- Mock `ConnectError`. Assert transient error with `error_type: "unreachable"`.
- `test_check_job_poll_interval_is_3_seconds` -- Monkeypatch `asyncio.sleep` to record calls. Call with `wait=10`. Assert sleep was called with `3` each time.
- `test_check_job_wait_capped_at_45` -- Call with `wait=120`. Monkeypatch `asyncio.sleep` to no-op. Count poll iterations. Assert total elapsed does not exceed 45s (max 15 iterations of 3s).
- `test_mcp_check_job_registered` -- Verify `check_job` appears in `mcp.list_tools()`.

**Testing pattern for monkeypatching asyncio.sleep:**

```python
@pytest.mark.anyio
@respx.mock
async def test_check_job_poll_interval_is_3_seconds(templates_dir, monkeypatch):
    sleep_calls = []
    async def mock_sleep(seconds):
        sleep_calls.append(seconds)
    monkeypatch.setattr(comfyclaude.comfyui.asyncio, "sleep", mock_sleep)

    # Mock history to always return running
    respx.get(f"{COMFYUI_URL}/history/abc-123").mock(
        return_value=httpx.Response(200, json={
            "abc-123": {"outputs": {}, "status": {"status_str": "", "completed": False, "messages": []}}
        })
    )
    await comfyclaude.comfyui.check_job("abc-123", wait=10)
    assert all(s == 3 for s in sleep_calls)
```

**Note on test fixture reuse:** The `check_job` tests do NOT need the `templates_dir` / `sample_templates` fixtures since `check_job` does not read template files. However, the `templates_dir` fixture also sets `COMFYUI_URL` and reloads modules, so it IS needed for correct URL configuration. Either reuse `templates_dir` or create a lighter fixture that only sets `COMFYUI_URL` and reloads `comfyclaude.config` and `comfyclaude.comfyui`.

### Anti-Pattern Prevention

- Do NOT import from `comfyclaude.templates` in this story -- `check_job` has no template involvement.
- Do NOT create a persistent `httpx.AsyncClient` at module level -- create per-request. Persistent client is Phase 2.
- Do NOT add `get_image` tool -- that is Story 2.4.
- Do NOT modify `queue_prompt` or any existing functions -- this story only adds new functions.
- Do NOT construct error dicts manually -- use `terminal_error()` / `transient_error()` from `comfyclaude.errors`.
- Do NOT log with `print()` -- use `logging.getLogger(__name__)`.
- Do NOT use `time.sleep()` -- use `asyncio.sleep()` for the async polling loop.
- Do NOT poll more than 15 times (45s / 3s = 15 iterations max).
- Do NOT add any image retrieval or file path resolution logic -- that is Story 2.4.

### Deferred Work Awareness

From prior stories (tracked in `deferred-work.md`):
- **Uncaught httpx.RequestError subclasses** -- `ReadError`, `RemoteProtocolError`, etc. escape as unhandled exceptions in `queue_prompt`. The same pattern applies to `check_job`. Consistent with the existing codebase approach.
- **JSONDecodeError outside try/except** -- `response.json()` can fail on non-JSON responses. Same pattern as `queue_prompt`.
- **Config import-time evaluation** -- Tests must reload both `config` and `comfyui` modules when overriding env vars.

### Project Structure Notes

- `comfyclaude/comfyui.py` grows with `check_job` + helpers -- this is expected per architecture (Job Monitoring maps to `comfyui.py`)
- Tests extend `tests/test_comfyui.py` -- same file will be further extended by Story 2.4
- No new files are created in this story

### References

- [Source: _bmad-output/planning-artifacts/architecture.md#Architectural Boundaries] -- ComfyUI HTTP Boundary (comfyui.py)
- [Source: _bmad-output/planning-artifacts/architecture.md#Async Architecture] -- asyncio.sleep for polling
- [Source: _bmad-output/planning-artifacts/architecture.md#Tool Return Format] -- dict with status field
- [Source: _bmad-output/planning-artifacts/architecture.md#Error Construction Pattern] -- transient_error/terminal_error usage
- [Source: _bmad-output/planning-artifacts/architecture.md#Tool Docstring Pattern] -- Rich docstrings for Claude Code
- [Source: _bmad-output/planning-artifacts/architecture.md#Test Organization] -- tests/ directory, respx for HTTP mocking
- [Source: _bmad-output/planning-artifacts/architecture.md#Requirements to Structure Mapping] -- Job Monitoring (FR14-18) -> comfyui.py
- [Source: _bmad-output/planning-artifacts/epics.md#Story 2.3] -- Acceptance criteria source
- [Source: _bmad-output/planning-artifacts/prd.md#FR14] -- Poll job with configurable wait
- [Source: _bmad-output/planning-artifacts/prd.md#FR15] -- Structured job status return
- [Source: _bmad-output/planning-artifacts/prd.md#FR16] -- 3s poll interval, 45s cap
- [Source: _bmad-output/planning-artifacts/prd.md#FR17] -- Early return on completion
- [Source: _bmad-output/planning-artifacts/prd.md#FR18] -- Non-blocking single status check
- [Source: _bmad-output/planning-artifacts/prd.md#NFR1] -- 30-second HTTP timeout
- [Source: _bmad-output/planning-artifacts/prd.md#NFR2] -- 3s sleep intervals, 45s cap, early return
- [Source: _bmad-output/implementation-artifacts/2-2-job-submission-with-input-injection-and-seed-randomization.md] -- Previous story context, comfyui.py patterns
- [Source: _bmad-output/implementation-artifacts/1-2-fastmcp-server-with-stdio-transport-and-startup-validation.md] -- Server.py mcp instance
- [Source: _bmad-output/implementation-artifacts/deferred-work.md] -- Known deferred items

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

None — all tests passed on first run.

### Completion Notes List

- Implemented `_fetch_job_status()` helper that queries `GET /history/{prompt_id}` and returns normalized internal state (pending/running/completed/failed)
- Implemented `check_job()` with single non-blocking check (wait=0) and polling loop (wait>0) with 3s intervals capped at 45s
- Implemented `_format_result()` to convert internal state to MCP tool response format, using `terminal_error("generation_failed", ...)` for failed jobs and `transient_error("unreachable", ...)` for connection errors
- Registered `check_job` MCP tool in `server.py` with rich docstring for Claude Code usage guidance
- Added 12 tests covering all acceptance criteria: single check (completed/pending/running), polling with completion, early return, timeout, failure, unreachable, poll interval verification, wait cap, and MCP tool registration
- Full test suite: 119 tests pass, 0 regressions

### Change Log

- 2026-03-29: Implemented Story 2.3 — Job Monitoring & Completion Polling (check_job with polling support)

### File List

- comfyclaude/comfyui.py (modified) — Added `asyncio` import, `DEFAULT_POLL_INTERVAL`/`MAX_POLL_DURATION` constants, `_fetch_job_status()`, `_format_result()`, `check_job()` functions
- comfyclaude/server.py (modified) — Added `@mcp.tool()` registration for `check_job`
- tests/test_comfyui.py (modified) — Added 12 test cases for check_job functionality
