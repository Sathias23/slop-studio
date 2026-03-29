# Story 1.1: Package Structure, Configuration & Error Types

Status: done

## Story

As a developer building on ComfyClaude,
I want the project to have a properly structured Python package with typed configuration and error handling,
So that all future tools have consistent error responses and configurable behavior from day one.

## Acceptance Criteria

1. **Given** the repository has been cloned and `uv` is available
   **When** I run `uv sync`
   **Then** `fastmcp` and `httpx` are installed as dependencies, and `pytest` and `respx` as dev dependencies

2. **Given** the `comfyclaude/` package exists
   **When** I inspect the module structure
   **Then** `__init__.py`, `config.py`, and `errors.py` are present

3. **Given** no environment variables are set
   **When** `config.py` is imported
   **Then** `COMFYUI_URL` defaults to `http://localhost:8188`, `TEMPLATES_DIR` defaults to `./templates`, and `OUTPUT_DIR` defaults to `./output` (FR26, FR27, FR28)

4. **Given** environment variables `COMFYUI_URL`, `COMFYCLAUDE_TEMPLATES_DIR`, and `COMFYCLAUDE_OUTPUT_DIR` are set
   **When** `config.py` is imported
   **Then** the constants reflect the environment variable values

5. **Given** `errors.py` is imported
   **When** I call `transient_error("unreachable", "Cannot connect to ComfyUI")`
   **Then** it returns `{"status": "error", "error": "Cannot connect to ComfyUI", "error_type": "unreachable", "retry_suggested": True}` (FR22, FR23)

6. **Given** `errors.py` is imported
   **When** I call `terminal_error("invalid_workflow", "Node type not found")`
   **Then** it returns `{"status": "error", "error": "Node type not found", "error_type": "invalid_workflow", "retry_suggested": False}` (FR22, FR24)

7. **Given** the error helpers exist
   **When** any error response is constructed
   **Then** it always contains `status`, `error`, `error_type`, and `retry_suggested` fields with context-rich messages (FR25)

8. **Given** the story is complete
   **When** I run `uv run pytest tests/test_errors.py`
   **Then** all error helper tests pass with respx/pytest

## Tasks / Subtasks

- [x] Task 1: Add project dependencies (AC: #1)
  - [x] 1.1 Run `uv add fastmcp httpx` to add runtime dependencies
  - [x] 1.2 Run `uv add --dev pytest respx` to add dev dependencies
  - [x] 1.3 Verify `uv sync` installs everything cleanly
- [x] Task 2: Create `comfyclaude/` package structure (AC: #2)
  - [x] 2.1 Create `comfyclaude/__init__.py` (package marker, version string)
  - [x] 2.2 Create `comfyclaude/config.py` (module-level constants from env vars)
  - [x] 2.3 Create `comfyclaude/errors.py` (ErrorResponse dataclass + helpers)
- [x] Task 3: Implement `config.py` (AC: #3, #4)
  - [x] 3.1 Define three module-level constants reading from env vars with defaults
- [x] Task 4: Implement `errors.py` (AC: #5, #6, #7)
  - [x] 4.1 Define `ErrorResponse` dataclass with four fields
  - [x] 4.2 Implement `transient_error()` helper returning dict via `asdict()`
  - [x] 4.3 Implement `terminal_error()` helper returning dict via `asdict()`
- [x] Task 5: Write tests (AC: #8)
  - [x] 5.1 Create `tests/` directory with `conftest.py`
  - [x] 5.2 Create `tests/test_errors.py` covering all error helpers
  - [x] 5.3 Create `tests/test_config.py` covering default and env var overrides
  - [x] 5.4 Verify all tests pass with `uv run pytest`

### Review Findings

- [x] [Review][Patch] test_config.py reload state pollution — env-override tests call `importlib.reload` then `monkeypatch` reverts the env var, but never re-reloads the module, leaving constants at overridden values for any subsequent test. Fixed: added `autouse` fixture in `conftest.py` that reloads `comfyclaude.config` after each test. [tests/test_config.py:18-33]
- [x] [Review][Patch] pyproject.toml description placeholder not replaced — `description = "Add your description here"` [pyproject.toml:4]
- [x] [Review][Defer] Config constants evaluated at import time — by architectural design (spec mandates module-level constants); pre-existing pattern. [comfyclaude/config.py:3-5] — deferred, pre-existing
- [x] [Review][Defer] Relative path defaults are cwd-dependent — `./templates` and `./output` are spec-mandated defaults (FR26-28); no path resolution required. [comfyclaude/config.py:4-5] — deferred, pre-existing
- [x] [Review][Defer] error_type and message accept empty strings — spec explicitly prohibits runtime validation ("taxonomy enforced by convention"). [comfyclaude/errors.py:12-19] — deferred, pre-existing
- [x] [Review][Defer] fastmcp pinned with floor only (>=3.1.1) — dev notes explicitly prohibit pinning; uv.lock provides reproducibility. [pyproject.toml:8] — deferred, pre-existing
- [x] [Review][Defer] Empty env var ("") bypasses defaults — no validation required per spec anti-pattern rules. [comfyclaude/config.py:3-5] — deferred, pre-existing

## Dev Notes

### Architecture Compliance

**Module structure** — Architecture mandates this layout under `comfyclaude/`:
```
comfyclaude/
  __init__.py
  config.py
  errors.py
```
These three files are the only deliverables for this story. Do NOT create `server.py`, `comfyui.py`, `templates.py`, or `init.py` — those belong to later stories.

**Error handling is the #1 cross-cutting concern** — Every future MCP tool in this project returns dicts through `transient_error()` / `terminal_error()`. Getting this right now prevents inconsistency across all of Epic 2, 3, and 4.

### Technical Requirements

**`config.py` implementation** — Three module-level constants, no config class:
```python
import os

COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://localhost:8188")
TEMPLATES_DIR = os.environ.get("COMFYCLAUDE_TEMPLATES_DIR", "./templates")
OUTPUT_DIR = os.environ.get("COMFYCLAUDE_OUTPUT_DIR", "./output")
```
Note the env var names: `COMFYUI_URL` (no prefix), `COMFYCLAUDE_TEMPLATES_DIR`, `COMFYCLAUDE_OUTPUT_DIR`. The asymmetry is intentional per PRD FR26-28.

**`errors.py` implementation** — Dataclass + two helper functions:
```python
from dataclasses import dataclass, asdict

@dataclass
class ErrorResponse:
    status: str = "error"
    error: str = ""
    error_type: str = ""
    retry_suggested: bool = False

def transient_error(error_type: str, message: str) -> dict:
    """Create error response for retryable failures."""
    return asdict(ErrorResponse(error=message, error_type=error_type, retry_suggested=True))

def terminal_error(error_type: str, message: str) -> dict:
    """Create error response for non-retryable failures."""
    return asdict(ErrorResponse(error=message, error_type=error_type, retry_suggested=False))
```

**Error type taxonomy** (enforce by convention, not runtime validation):
- **Transient** (retry_suggested=True): `unreachable`, `generation_failed`, `storage_error`
- **Terminal** (retry_suggested=False): `invalid_inputs`, `invalid_workflow`, `model_not_found`, `directory_not_found`, `permission_denied`, `completed_no_output`

**`__init__.py`** — Minimal package marker:
```python
"""ComfyClaude - MCP server for conversational image generation via ComfyUI."""
```
No version string needed for MVP (not published to PyPI).

### Library & Framework Requirements

**CRITICAL VERSION NOTE:** The architecture document references FastMCP 3.1.1, but the latest stable version on PyPI is **2.14.6** (released 2026-03-27). Use whatever version `uv add fastmcp` resolves — do NOT pin to a non-existent version. The FastMCP API for `@mcp.tool()` decorators and stdio transport is stable across 2.x.

| Package | Purpose | Install Command |
|---------|---------|-----------------|
| fastmcp | MCP server framework (used in Story 1.2, installed now) | `uv add fastmcp` |
| httpx | Async HTTP client for ComfyUI API (used in Story 2.2+, installed now) | `uv add httpx` |
| pytest | Testing framework | `uv add --dev pytest` |
| respx | httpx mock responses for tests | `uv add --dev respx` |

### File Structure Requirements

Create these files and directories:
```
comfyclaude/
  __init__.py
  config.py
  errors.py
tests/
  conftest.py
  test_errors.py
  test_config.py
```

Do NOT create or modify:
- `main.py` — Leave the existing stub as-is; Story 1.2 will update it
- `comfyclaude/server.py` — Story 1.2
- `comfyclaude/comfyui.py` — Story 2.2
- `comfyclaude/templates.py` — Story 2.1
- `templates/` directory — Story 2.1
- `output/` directory — Story 2.4

### Testing Requirements

- Use `pytest` as the test framework
- Place tests in `tests/` directory (not co-located with source)
- Test naming: `test_<what_it_does>` (e.g., `test_transient_error_sets_retry_true`)
- `tests/conftest.py` can be empty or contain shared fixtures for future stories
- **test_errors.py** must cover:
  - `transient_error()` returns dict with all four required fields
  - `transient_error()` sets `retry_suggested=True` and `status="error"`
  - `terminal_error()` returns dict with all four required fields
  - `terminal_error()` sets `retry_suggested=False` and `status="error"`
  - Both helpers preserve the `error` message and `error_type` exactly as passed
- **test_config.py** must cover:
  - Default values when no env vars are set
  - Env var overrides for all three constants
  - Test env vars using monkeypatch or by setting env before import

### Anti-Pattern Prevention

- Do NOT create a Config class or use pydantic for configuration — three module-level constants is the architectural decision
- Do NOT add runtime validation of error_type values — taxonomy is enforced by convention
- Do NOT use `print()` anywhere — logging to stderr via `logging` module is the pattern (though logging setup itself is Story 1.2)
- Do NOT construct error dicts manually anywhere — always use the helper functions
- Do NOT add any MCP tool registration — that's Story 1.2

### Project Structure Notes

- The project was initialized with `uv init` (application template) — `pyproject.toml` and `.python-version` already exist
- `.python-version` pins Python 3.11+ (managed by uv)
- No `src/` layout — flat package layout per architecture decision
- `main.py` exists at project root as entry point (will be modified in Story 1.2)

### References

- [Source: _bmad-output/planning-artifacts/architecture.md#Module Structure & Distribution] — Package layout decision
- [Source: _bmad-output/planning-artifacts/architecture.md#Error Handling] — ErrorResponse dataclass decision
- [Source: _bmad-output/planning-artifacts/architecture.md#Config Access Pattern] — Module-level constants pattern
- [Source: _bmad-output/planning-artifacts/architecture.md#Error Construction Pattern] — transient_error/terminal_error helpers
- [Source: _bmad-output/planning-artifacts/architecture.md#Test Organization] — Test placement and naming
- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.1] — Acceptance criteria source
- [Source: _bmad-output/planning-artifacts/prd.md#Error Handling FR22-25] — Error response structure requirements
- [Source: _bmad-output/planning-artifacts/prd.md#Configuration FR26-28] — Environment variable specifications

## Dev Agent Record

### Agent Model Used
Claude Opus 4.6

### Debug Log References
- All 14 tests pass on first run (6 config, 8 errors)

### Completion Notes List
- Task 1: Added fastmcp 3.1.1, httpx 0.28.1 as runtime deps; pytest 9.0.2, respx 0.22.0 as dev deps. `uv sync` clean.
- Task 2: Created comfyclaude/ package with __init__.py, config.py, errors.py per architecture spec.
- Task 3: config.py implements three module-level constants with env var overrides (COMFYUI_URL, COMFYCLAUDE_TEMPLATES_DIR, COMFYCLAUDE_OUTPUT_DIR).
- Task 4: errors.py implements ErrorResponse dataclass with transient_error() and terminal_error() helpers returning dicts via asdict().
- Task 5: 14 tests covering all error helpers (exact output, field presence, retry flag, message preservation) and config defaults + env var overrides via monkeypatch/importlib.reload.

### Change Log
- 2026-03-29: Story 1.1 implementation complete — package structure, config, errors, and tests.

### File List
- comfyclaude/__init__.py (new)
- comfyclaude/config.py (new)
- comfyclaude/errors.py (new)
- tests/__init__.py (new)
- tests/conftest.py (new)
- tests/test_errors.py (new)
- tests/test_config.py (new)
- pyproject.toml (modified)
- uv.lock (modified)
