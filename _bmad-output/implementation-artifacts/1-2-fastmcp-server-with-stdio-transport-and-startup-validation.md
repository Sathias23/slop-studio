# Story 1.2: FastMCP Server with stdio Transport & Startup Validation

Status: done

## Story

As a developer using Claude Code,
I want to start the ComfyClaude MCP server and have it validate that ComfyUI is reachable,
So that I get immediate feedback if my setup is broken rather than discovering it on the first tool call.

## Acceptance Criteria

1. **Given** `comfyclaude/server.py` exists
   **When** I inspect it
   **Then** it creates a `FastMCP` server instance with a descriptive name and registers it for stdio transport (FR30)

2. **Given** `main.py` exists as the entry point
   **When** I run `uv run main.py`
   **Then** the server starts in stdio mode, ready to receive MCP tool calls

3. **Given** ComfyUI is running at the configured `COMFYUI_URL`
   **When** the server starts
   **Then** it successfully validates connectivity and begins accepting MCP requests (NFR3)

4. **Given** ComfyUI is NOT running at the configured `COMFYUI_URL`
   **When** the server starts
   **Then** it fails fast with a clear error message indicating ComfyUI is unreachable, and does not start the MCP server (NFR3)

5. **Given** the HTTP timeout is configured
   **When** any HTTP request is made to ComfyUI during startup validation
   **Then** the request uses a 30-second timeout (NFR1)

6. **Given** the server is running
   **When** the MCP client sends an initialize handshake
   **Then** the server responds successfully with an empty tools list (tool registration is completed in Epic 2 stories)

7. **Given** the repository root
   **When** I inspect `README.md`
   **Then** it contains: setup instructions (clone, uv sync, set COMFYUI_URL, add MCP config snippet), and the known-working ComfyUI version the server was tested against

8. **Given** the story is complete
   **When** I run `uv run pytest tests/test_server.py`
   **Then** all server startup and connectivity tests pass with mocked HTTP via respx

## Tasks / Subtasks

- [x] Task 1: Create `comfyclaude/server.py` with FastMCP instance and lifespan (AC: #1, #3, #4, #5)
  - [x] 1.1 Create `comfyclaude/server.py` with `FastMCP("comfyclaude")` instance
  - [x] 1.2 Implement a lifespan function that performs ComfyUI connectivity check using `httpx.AsyncClient`
  - [x] 1.3 The lifespan must `GET {COMFYUI_URL}/system_stats` with 30s timeout
  - [x] 1.4 On success: log to stderr and allow server to start
  - [x] 1.5 On failure (connection refused, timeout, non-200): raise an exception to prevent server from starting
- [x] Task 2: Update `main.py` entry point (AC: #2)
  - [x] 2.1 Replace the current stub with `mcp.run(transport="stdio")`
  - [x] 2.2 Import the `mcp` instance from `comfyclaude.server`
- [x] Task 3: Create `README.md` (AC: #7)
  - [x] 3.1 Write setup instructions (clone, uv sync, COMFYUI_URL, MCP config snippet)
  - [x] 3.2 Include MCP config JSON snippet with `uv run --directory` command
  - [x] 3.3 Note known-working ComfyUI version (current stable)
- [x] Task 4: Write tests in `tests/test_server.py` (AC: #8)
  - [x] 4.1 Test that server starts successfully when ComfyUI health check returns 200
  - [x] 4.2 Test that server fails fast when ComfyUI is unreachable (connection error)
  - [x] 4.3 Test that server fails fast when ComfyUI returns non-200
  - [x] 4.4 Test that HTTP timeout is 30 seconds
  - [x] 4.5 Test that the lifespan uses the configured `COMFYUI_URL` from `config.py`

### Review Findings

- [x] [Review][Decision] README ComfyUI version unspecified — updated to "Latest stable release recommended (not nightly builds)" per user direction [README.md:11]
- [x] [Review][Patch] README MCP config JSON is malformed — dismissed on verification; actual file contains valid JSON (diff rendering artifact) [README.md, MCP Configuration section]
- [x] [Review][Defer] Invalid/empty COMFYUI_URL causes unhandled exception — `httpx.InvalidURL` or `httpx.UnsupportedProtocol` propagates without log; already tracked in deferred-work.md from Story 1.1 [comfyclaude/server.py:18] — deferred, pre-existing
- [x] [Review][Defer] Uncaught httpx.RemoteProtocolError and other HTTPError subclasses propagate without logging — spec explicitly lists only three required exception types [comfyclaude/server.py:19-32] — deferred, out of scope
- [x] [Review][Defer] Trailing slash in COMFYUI_URL produces double-slash URL path — spec doesn't require URL normalization; acceptable per current design [comfyclaude/server.py:18] — deferred, pre-existing
- [x] [Review][Defer] TimeoutException log message says "connection timed out" regardless of timeout subtype (read/pool/connect) — cosmetic; all subtypes are still correctly handled and logged [comfyclaude/server.py:23-25] — deferred, low severity

## Dev Notes

### Architecture Compliance

**Module boundaries** — `server.py` is the only module that imports FastMCP. It is the MCP protocol boundary. The `mcp` instance lives here, and all future `@mcp.tool()` registrations will be added in this file (starting in Epic 2).

**Lifespan pattern** — FastMCP 3.1.1 supports a lifespan parameter on the `FastMCP` constructor. Use `@asynccontextmanager` for the startup health check:

```python
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
import logging
import httpx
from fastmcp import FastMCP
from comfyclaude.config import COMFYUI_URL

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """Validate ComfyUI connectivity before accepting requests."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(f"{COMFYUI_URL}/system_stats")
        response.raise_for_status()
    logger.info("ComfyUI reachable at %s", COMFYUI_URL)
    yield {}

mcp = FastMCP("comfyclaude", lifespan=lifespan)
```

**Fail-fast behavior (NFR3)** — If `httpx.ConnectError`, `httpx.TimeoutException`, or any `httpx.HTTPStatusError` is raised during the lifespan startup, it propagates uncaught and prevents the MCP server from entering its message loop. This is the correct behavior — the lifespan runs before stdio processing begins. Log a clear error message to stderr before the exception propagates.

**No tools yet** — Do NOT register any `@mcp.tool()` functions in this story. The server will respond to MCP initialize with an empty tools list. Tool registration begins in Story 2.1.

### Technical Requirements

**`main.py` update** — Replace the current stub entirely:
```python
from comfyclaude.server import mcp

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

This is the only change to `main.py`. The `mcp.run()` method blocks and handles stdio read/write.

**HTTP timeout** — Use 30-second timeout per NFR1. Pass `timeout=30.0` to `httpx.AsyncClient()`. This applies to the startup health check. Future stories will use the same timeout pattern for ComfyUI API calls.

**Logging setup** — Use `logging.getLogger(__name__)` in `server.py`. Log to stderr (Python logging defaults to stderr). Do NOT configure root logger formatting — default is fine for MVP. Do NOT use `print()`.

**Health check endpoint** — Use `GET /system_stats` on ComfyUI. This is a lightweight endpoint that returns system info and confirms ComfyUI is responsive. Do NOT use `/prompt` or other heavier endpoints for the health check.

### Previous Story Intelligence

**Story 1.1 established:**
- `comfyclaude/config.py` with `COMFYUI_URL`, `TEMPLATES_DIR`, `OUTPUT_DIR` as module-level constants
- `comfyclaude/errors.py` with `ErrorResponse` dataclass + `transient_error()` / `terminal_error()` helpers
- `comfyclaude/__init__.py` as package marker
- `tests/conftest.py` with `autouse` fixture that reloads `comfyclaude.config` after each test (prevents env var bleed between tests)
- `pyproject.toml` with `fastmcp>=3.1.1` and `httpx>=0.28.1` as runtime deps, `pytest>=9.0.2` and `respx>=0.22.0` as dev deps

**Review findings from 1.1:**
- Config constants are evaluated at import time — use `importlib.reload` in tests when testing with different env vars
- Empty env var `""` bypasses defaults — consider a startup check in this story (see deferred work item)
- The `reload_config` autouse fixture in `conftest.py` already handles config cleanup between tests

**Git patterns from 1.1:**
- FastMCP 3.1.1 is the installed version (despite architecture doc mentioning it)
- Tests use `pytest` with `monkeypatch` for env var manipulation
- Clean separation: source in `comfyclaude/`, tests in `tests/`

### Library & Framework Requirements

| Package | Version (installed) | Usage in this story |
|---------|-------------------|---------------------|
| fastmcp | 3.1.1 | `FastMCP` class, `lifespan` parameter, `mcp.run(transport="stdio")` |
| httpx | 0.28.1 | `AsyncClient` for ComfyUI health check in lifespan |
| pytest | 9.0.2 | Test framework |
| respx | 0.22.0 | Mock httpx requests in tests |

**Key FastMCP API (v3.1.1):**
- `from fastmcp import FastMCP` — server class
- `FastMCP(name, lifespan=...)` — constructor with optional lifespan
- `mcp.run(transport="stdio")` — blocking entry point (calls `anyio.run` internally)
- Lifespan: `@asynccontextmanager` function accepting `(server: FastMCP)` and yielding a dict

### File Structure Requirements

**Create:**
```
comfyclaude/server.py    # FastMCP instance + lifespan health check
tests/test_server.py     # Server startup + connectivity tests
README.md                # Setup instructions
```

**Modify:**
```
main.py                  # Replace stub with mcp.run(transport="stdio")
```

**Do NOT create or modify:**
- `comfyclaude/comfyui.py` — Story 2.2 (the health check httpx call lives in the lifespan, not in a separate module)
- `comfyclaude/templates.py` — Story 2.1
- `comfyclaude/init.py` — Story 4.1
- Any `@mcp.tool()` registrations — Story 2.1+

### Testing Requirements

Tests in `tests/test_server.py` using `respx` to mock ComfyUI HTTP responses:

**Test the lifespan function directly** — Extract it and test it in isolation. Do NOT try to spin up the full MCP stdio server in tests. Test the lifespan as an async context manager.

**Required test cases:**
- `test_lifespan_succeeds_when_comfyui_reachable` — Mock `GET /system_stats` returning 200; assert lifespan enters and yields without error
- `test_lifespan_fails_when_comfyui_unreachable` — Mock connection error; assert lifespan raises `httpx.ConnectError`
- `test_lifespan_fails_on_non_200_response` — Mock 500 response; assert lifespan raises `httpx.HTTPStatusError`
- `test_lifespan_uses_configured_url` — Set `COMFYUI_URL` to custom value via monkeypatch + reload; verify the health check hits the correct URL
- `test_lifespan_uses_30s_timeout` — Verify the httpx client is created with `timeout=30.0`

**respx usage pattern for these tests:**
```python
import respx
import httpx
import pytest
from comfyclaude.config import COMFYUI_URL

@respx.mock
async def test_lifespan_succeeds_when_comfyui_reachable():
    respx.get(f"{COMFYUI_URL}/system_stats").mock(return_value=httpx.Response(200, json={"system": {}}))
    # Enter lifespan context manager, assert no exception
```

Use `@pytest.mark.anyio` or `@pytest.mark.asyncio` for async tests. Add `anyio` or `pytest-asyncio` as a dev dependency if needed (check if FastMCP already brings it in).

### Anti-Pattern Prevention

- Do NOT create a separate `comfyui.py` module for the health check — the startup validation lives in the lifespan, which is part of `server.py`. The `comfyui.py` module (Story 2.2) will handle runtime ComfyUI API calls.
- Do NOT register any MCP tools — this story establishes the server skeleton only.
- Do NOT use `print()` — use `logging.getLogger(__name__)` for all output.
- Do NOT catch and swallow the lifespan exception — let it propagate so the server fails to start.
- Do NOT add a config class or modify `config.py` — import `COMFYUI_URL` directly from `comfyclaude.config`.
- Do NOT add structured error responses to the startup check — the lifespan failure is a startup crash, not an MCP error response. The `transient_error()` / `terminal_error()` helpers are for MCP tool responses in later stories.

### Deferred Work Awareness

From Story 1.1 code review:
- Empty env var `""` bypasses defaults in `config.py` — if `COMFYUI_URL=""`, the health check will fail with a clear connection error, which is acceptable behavior. No special handling needed.
- Config constants evaluated at import time — this means `server.py` must import `COMFYUI_URL` at module level. Tests that need a different URL must monkeypatch the env var and reload `comfyclaude.config`, then either reload `comfyclaude.server` or pass the URL differently.

### Project Structure Notes

- Alignment with architecture: `server.py` is the MCP protocol boundary per architecture doc
- `main.py` at project root is the entry point per architecture's `Module Structure & Distribution` section
- FastMCP handles all JSON-RPC protocol details — `server.py` only needs to create the instance and define the lifespan

### References

- [Source: _bmad-output/planning-artifacts/architecture.md#Async Architecture] — Async throughout, FastMCP handles async natively
- [Source: _bmad-output/planning-artifacts/architecture.md#Module Structure & Distribution] — main.py entry point, server.py for FastMCP
- [Source: _bmad-output/planning-artifacts/architecture.md#Architectural Boundaries] — server.py is MCP protocol boundary
- [Source: _bmad-output/planning-artifacts/architecture.md#Logging] — stderr only, Python logging module
- [Source: _bmad-output/planning-artifacts/architecture.md#Test Organization] — tests/ directory, respx for HTTP mocking
- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.2] — Acceptance criteria source
- [Source: _bmad-output/planning-artifacts/prd.md#NFR1] — 30-second HTTP timeout
- [Source: _bmad-output/planning-artifacts/prd.md#NFR3] — Fail-fast startup if ComfyUI unreachable
- [Source: _bmad-output/planning-artifacts/prd.md#FR30] — stdio transport
- [Source: _bmad-output/implementation-artifacts/1-1-package-structure-configuration-and-error-types.md] — Previous story context
- [Source: _bmad-output/implementation-artifacts/deferred-work.md] — Known deferred items from Story 1.1

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

No issues encountered during implementation.

### Completion Notes List

- Created `comfyclaude/server.py` with `FastMCP("comfyclaude")` instance and async lifespan that validates ComfyUI connectivity via `GET /system_stats` with 30s httpx timeout. Fail-fast behavior: ConnectError, TimeoutException, and HTTPStatusError are logged to stderr and re-raised to prevent server startup.
- Updated `main.py` to import `mcp` from `comfyclaude.server` and call `mcp.run(transport="stdio")`.
- Created `README.md` with setup instructions (clone, uv sync, COMFYUI_URL env var), MCP config JSON snippet with `uv run --directory`, and known-working ComfyUI version note.
- Created `tests/test_server.py` with 6 tests using `respx` mocks: lifespan success (200), connection error, non-200 response, custom URL via monkeypatch+reload, 30s timeout verification via AsyncClient.__init__ monkeypatch, and timeout exception propagation.
- All 88 tests pass (6 new + 82 existing), zero regressions.

### File List

- `comfyclaude/server.py` (new) — FastMCP instance + lifespan health check
- `main.py` (modified) — Entry point updated to use mcp.run(transport="stdio")
- `README.md` (modified) — Setup instructions and MCP config
- `tests/test_server.py` (new) — Server startup and connectivity tests

### Change Log

- 2026-03-29: Implemented Story 1.2 — FastMCP server with stdio transport and startup validation. Created server.py with lifespan health check, updated main.py entry point, added README.md, and 6 new tests.
