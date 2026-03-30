---
stepsCompleted:
  - 1
  - 2
  - 3
  - 4
  - 5
  - 6
  - 7
  - 8
inputDocuments:
  - "prd.md"
  - "product-brief-ComfyClaude.md"
  - "product-brief-ComfyClaude-distillate.md"
workflowType: 'architecture'
lastStep: 8
status: 'complete'
completedAt: '2026-03-29'
project_name: 'ComfyClaude'
user_name: 'Brad'
date: '2026-03-29'
---

# Architecture Decision Document

_This document builds collaboratively through step-by-step discovery. Sections are appended as we work through each architectural decision together._

## Project Context Analysis

### Requirements Overview

**Functional Requirements:**
30 functional requirements across 7 categories:
- **Template Discovery (FR1-3):** List and inspect workflow templates with rich metadata. Claude Code selects templates based on tool descriptions and metadata — no user guidance needed.
- **Template Management (FR4-8):** Full CRUD for workflow templates with schema validation on write. Path traversal protection on template names.
- **Image Generation (FR9-13):** Job submission from template + inputs + optional aspect ratio. Server handles input injection into workflow nodes, seed randomization across all nodes, and resolution mapping from aspect ratio labels.
- **Job Monitoring (FR14-18):** Completion polling with configurable wait (3s interval, 45s cap, early return). Supports both blocking poll and non-blocking single check.
- **Image Retrieval (FR19-21):** Return absolute file paths for completed generations. Output organized by date. Filename sanitization via `os.path.basename()`.
- **Error Handling (FR22-25):** Structured error responses with typed classification (3 transient, 6 terminal). Error messages contextualized for Claude Code self-correction.
- **Configuration & OOB (FR26-30):** Three environment variables with sensible defaults. Two starter templates ship out of the box. stdio transport.

**Non-Functional Requirements:**
- HTTP timeout: 30s per ComfyUI API call
- Poll interval: 3s, poll cap: 45s with early return
- Fail-fast startup if ComfyUI unreachable
- No caching required (single-user, filesystem scan per call)
- Synchronous file I/O sufficient (no streaming/chunked transfers)
- No authentication, no multi-user, no cloud deployment

**Scale & Complexity:**
- Primary domain: Integration layer (MCP server wrapping HTTP API)
- Complexity level: Low
- Estimated architectural components: 4-5 modules (server entry point, template management, job orchestration, ComfyUI client, error types)

### Technical Constraints & Dependencies

- **Runtime:** Python 3.11+, managed by `uv`
- **Framework:** FastMCP (Python) for MCP server implementation
- **Transport:** stdio (single-user, local process communication)
- **HTTP client:** httpx for ComfyUI API communication
- **External dependency:** ComfyUI HTTP API (unversioned — `/prompt`, `/history/{id}`, `/view`)
- **Source extraction:** Core logic from cenobite-agent's `image_generation.py` — input injection, seed randomization, polling, error handling. Letta sandbox constraints (imports inside functions, JSON string returns) do not apply.
- **Template format:** `.json` + `.meta.json` pairs, filesystem-based, no database
- **Configuration:** Environment variables (`COMFYUI_URL`, `COMFYCLAUDE_TEMPLATES_DIR`, `COMFYCLAUDE_OUTPUT_DIR`)

### Cross-Cutting Concerns Identified

- **Error handling & classification** — Every tool must return structured errors with type and retry suggestion. This is the primary mechanism for Claude Code self-correction.
- **Path sanitization** — Template names and image filenames must be sanitized before any filesystem operations. Applies across template management and image retrieval.
- **Seed randomization** — All seed/noise_seed fields across all workflow nodes must be randomized on every submission. Prevents ComfyUI cache hits.
- **Template validation** — Schema validation on write affects both `add_template` and `update_template`. Must verify meta fields, node ID references, and resolution node validity.

## Starter Template Evaluation

### Primary Technology Domain

Python MCP server (integration layer) — not a web app, mobile app, or full-stack project. No traditional starter template CLI applies.

### Starter Options Considered

| Option | Description | Verdict |
|--------|-------------|---------|
| `uv init` (application) | Bare `pyproject.toml` + `main.py`, no src layout | Already used — good starting point for a single-purpose server |
| `uv init --lib` (library) | src/ layout with build system | Over-engineered — this isn't a distributable library for v1 |
| FastMCP example projects | Decorator-based tool registration, single-file examples | Good patterns to follow, but no scaffolding CLI |

### Selected Starter: `uv init` (application template) + FastMCP conventions

**Rationale for Selection:**
- Project already initialized with `uv init` — `pyproject.toml` and `.python-version` in place
- Application template (flat layout) is appropriate for a single-purpose MCP server that won't be published to PyPI
- FastMCP provides the framework conventions (decorator-based tools, automatic parameter validation, stdio transport) — no additional scaffolding needed
- Adding dependencies via `uv add fastmcp httpx` is all that's needed to establish the foundation

**Key Dependencies (Current Versions):**

| Package | Version | Purpose |
|---------|---------|---------|
| fastmcp | 3.1.1 | MCP server framework — tool registration, protocol handling, stdio transport |
| httpx | 0.28.1 | Async HTTP client for ComfyUI API communication |
| pytest | latest | Testing framework (dev dependency) |

**Architectural Decisions Provided by Framework:**

**Language & Runtime:**
- Python 3.11+, managed by `uv`
- Type hints for tool parameter validation (FastMCP uses these to generate MCP tool schemas)

**MCP Protocol Handling:**
- FastMCP handles JSON-RPC message parsing, tool dispatch, and response formatting
- Tools registered via `@mcp.tool()` decorator with automatic schema generation from type hints and docstrings
- stdio transport configured by default — no HTTP server needed

**Development Experience:**
- `uv run` for execution, `uv add` for dependency management
- FastMCP Inspector (`fastmcp dev`) for interactive tool testing in browser
- pytest for unit testing with mocked ComfyUI API

**Note:** Project initialization is already complete (`uv init` done). First implementation story should add dependencies and establish module structure.

## Core Architectural Decisions

### Decision Priority Analysis

**Critical Decisions (Block Implementation):**
1. Package layout with init command and bundled assets
2. Async tools with httpx.AsyncClient
3. Distribution via `uv run --directory` (MVP), `uvx` (Phase 2)

**Important Decisions (Shape Architecture):**
4. Dataclasses for error representation
5. Meta-only template validation (MVP)
6. pytest + respx for testing

**Deferred Decisions (Post-MVP):**
- PyPI packaging and `uvx` distribution (Phase 2)
- Cross-reference template validation — verify node_ids exist in workflow JSON (Phase 2)

### Module Structure & Distribution

**Decision:** Package layout with init command

```
slop_studio/
  __init__.py
  server.py          # FastMCP server + tool registration
  comfyui.py         # httpx.AsyncClient for ComfyUI API
  templates.py       # Template CRUD + meta-only validation
  errors.py          # Dataclass error types + helper functions
  init.py            # Init command — scaffold project folders
  assets/
    starter-templates/   # flux2_klein, flux2_klein_ultrawide
    claude-commands/     # Slash commands for art projects
    claude-md-template.md
main.py              # Entry point: server or init based on args
templates/           # Development-time templates (also source for assets)
tests/
```

**Rationale:** Init command needs bundled assets (starter templates, slash commands, CLAUDE.md template). Package layout keeps these organized and makes Phase 2 PyPI migration straightforward.

**MVP Distribution:**
- Clone repo once
- `uv run --directory ~/Projects/ComfyClaude init` to scaffold art project folders
- `.mcp.json` generated with `uv run --directory` pointing back to repo

**Phase 2 Distribution:**
- Publish to PyPI
- `uvx comfyclaude` runs the server
- `uvx comfyclaude init` scaffolds project folders
- `.mcp.json` command changes from `uv run --directory ...` to `uvx comfyclaude`

### Async Architecture

**Decision:** Async throughout

- `async def` for all MCP tool functions
- `httpx.AsyncClient` for ComfyUI API calls
- `asyncio.sleep` for polling intervals
- **Rationale:** Polling loop (sleep + HTTP check) is cleaner with asyncio. MCP protocol is inherently async. FastMCP handles async natively.

### Error Handling

**Decision:** Dataclasses for error representation

- `@dataclass` for `ErrorResponse` with fields: `status`, `error`, `error_type`, `retry_suggested`
- Error type taxonomy enforced by convention, not runtime validation
- Helper functions to construct transient vs terminal errors consistently
- **Rationale:** Lightweight, catches structural typos via constructor, no extra dependency. Sufficient rigor for a solo project with 8 tools.

### Template Validation

**Decision:** Meta-only validation for MVP

- Validate `.meta.json` structure: required fields present, input definitions well-formed, aspect_ratios and resolution_nodes structurally valid
- Do NOT inspect workflow JSON beyond basic "is it valid JSON with a dict structure"
- **Rationale:** Cross-reference validation (FR7's node_id checking) adds complexity without proportional value in MVP. User is the template author — they'll catch broken references when generation fails with a clear error.
- **Phase 2:** Add cross-reference validation (verify node_ids in meta reference real nodes in workflow JSON)

### Testing Strategy

**Decision:** pytest + respx

- `pytest` as test framework
- `respx` for httpx mock responses (ComfyUI API simulation)
- Dev dependency only: `uv add --dev pytest respx`
- **Rationale:** respx is purpose-built for httpx, provides clean declarative HTTP mocking without manual mock plumbing.

### Infrastructure & Deployment

**Decision:** Local-only, no infrastructure

- No CI/CD for MVP (solo developer, local use)
- No containerization (runs natively on macOS)
- Logging to stderr only (stdio transport constraint)
- No monitoring beyond ComfyUI's own UI
- **Rationale:** Single-user local tool. Infrastructure would be pure overhead.

## Implementation Patterns & Consistency Rules

### Pattern Categories Defined

**Critical Conflict Points Identified:** 7 areas where AI agents could make different choices

### Tool Return Format

All MCP tools return dicts with a consistent `status` field:

**Success:** `{"status": "success", ...data}`
**Error:** `{"status": "error", "error": str, "error_type": str, "retry_suggested": bool}`

Every tool, every path — same top-level shape. Claude Code can always check `status` first.

### Code Naming Conventions

Follow PEP 8 throughout:
- **Functions/variables:** `snake_case` — `list_templates`, `check_job`, `poll_interval`
- **Classes:** `PascalCase` — `ErrorResponse`, `TemplateMetadata`
- **Constants:** `UPPER_SNAKE` — `DEFAULT_POLL_INTERVAL`, `TRANSIENT_ERROR_TYPES`
- **Files/modules:** `snake_case` — `comfyui.py`, `templates.py`
- **MCP tool names:** `snake_case` matching function names — `list_templates`, `queue_prompt`
- **JSON fields in API responses:** `snake_case` — consistent with Python conventions and ComfyUI's own API

### Tool Docstring Pattern

FastMCP generates tool descriptions from docstrings. These are the primary interface for Claude Code to understand tool capabilities.

**Pattern:** First line is the tool summary. Following lines explain returns and when/why to use it.

```python
@mcp.tool()
async def list_templates() -> dict:
    """List all available workflow templates with summary metadata.

    Returns template names, models, descriptions, supported aspect ratios,
    and expected generation duration. Use this to discover available templates
    before calling queue_prompt.
    """
```

**Rules:**
- First line must be a complete, descriptive sentence
- Include what the tool returns
- Include when to use it relative to other tools (e.g., "call this before queue_prompt")
- Include parameter constraints in param docstrings when not obvious from types

### Config Access Pattern

Module-level constants read from environment variables at import time:

```python
COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://localhost:8188")
TEMPLATES_DIR = os.environ.get("COMFYCLAUDE_TEMPLATES_DIR", "./templates")
OUTPUT_DIR = os.environ.get("COMFYCLAUDE_OUTPUT_DIR", "./output")
```

- Three env vars, three constants — no config class needed
- Defaults match PRD specification
- Testable by patching module-level constants or setting env vars before import

### Error Construction Pattern

Helper functions enforce transient/terminal classification:

```python
def transient_error(error_type: str, message: str) -> dict:
    """Create error response for retryable failures."""
    return asdict(ErrorResponse(error=message, error_type=error_type, retry_suggested=True))

def terminal_error(error_type: str, message: str) -> dict:
    """Create error response for non-retryable failures."""
    return asdict(ErrorResponse(error=message, error_type=error_type, retry_suggested=False))
```

**Transient types** (use `transient_error`): `unreachable`, `generation_failed`, `storage_error`
**Terminal types** (use `terminal_error`): `invalid_inputs`, `invalid_workflow`, `model_not_found`, `directory_not_found`, `permission_denied`, `completed_no_output`

Agents MUST use these helpers — never construct error dicts manually.

### Test Organization

- Tests in `tests/` directory, not co-located
- Files mirror modules: `test_templates.py`, `test_comfyui.py`, `test_server.py`
- Shared fixtures in `tests/conftest.py` — respx mocks, sample template data
- Test naming: `test_<what_it_does>` — `test_list_templates_returns_all`, `test_queue_prompt_with_missing_input_returns_error`
- All ComfyUI HTTP calls mocked via respx — tests never hit a real ComfyUI instance

### Logging

- All output to `stderr` — stdout is reserved for MCP stdio protocol
- Use Python `logging` module, never `print()`
- Logger per module: `logger = logging.getLogger(__name__)`
- Levels: `INFO` for operations (job queued, template added), `ERROR` for failures, `DEBUG` for HTTP request/response detail
- No log formatting decisions for MVP — default Python format is fine

### Enforcement Guidelines

**All AI Agents MUST:**
- Return dicts with `status` field from every tool
- Use `transient_error()` / `terminal_error()` helpers for all error responses
- Write descriptive docstrings on all `@mcp.tool()` functions (FastMCP uses them)
- Follow PEP 8 naming throughout — no camelCase in Python code or JSON responses
- Place tests in `tests/` with `test_` prefix, mock all HTTP via respx
- Log to stderr via `logging`, never `print()`

## Project Structure & Boundaries

### Requirements to Structure Mapping

| FR Category | Module | Key Functions |
|---|---|---|
| Template Discovery (FR1-3) | `slop_studio/templates.py` | `list_templates()`, `get_template()` |
| Template Management (FR4-8) | `slop_studio/templates.py` | `add_template()`, `update_template()`, `delete_template()`, validation |
| Image Generation (FR9-13) | `slop_studio/server.py` + `slop_studio/comfyui.py` | `queue_prompt()`, input injection, seed randomization |
| Job Monitoring (FR14-18) | `slop_studio/comfyui.py` | `check_job()`, polling loop |
| Image Retrieval (FR19-21) | `slop_studio/comfyui.py` | `get_image()`, file path resolution, date organization |
| Error Handling (FR22-25) | `slop_studio/errors.py` | `ErrorResponse`, `transient_error()`, `terminal_error()` |
| Configuration (FR26-28) | `slop_studio/config.py` | Module-level constants from env vars |
| Out-of-Box (FR29-30) | `slop_studio/assets/` | Starter templates, init scaffolding |

### Complete Project Directory Structure

```
ComfyClaude/
├── main.py                          # Entry point: "serve" or "init" subcommand
├── pyproject.toml                   # Dependencies, project metadata, [project.scripts] for Phase 2
├── .python-version                  # Python 3.11+ pin (uv)
├── .gitignore
├── README.md                        # Setup instructions, MCP config snippet, quick-start
│
├── slop_studio/
│   ├── __init__.py                  # Package marker, version
│   ├── server.py                    # FastMCP server instance, @mcp.tool() registrations
│   ├── comfyui.py                   # httpx.AsyncClient wrapper: submit, poll, fetch image
│   ├── templates.py                 # Template CRUD, meta-only validation, filesystem ops
│   ├── errors.py                    # ErrorResponse dataclass, transient_error(), terminal_error()
│   ├── config.py                    # Module-level constants from env vars
│   ├── init.py                      # Init command: scaffold project folders
│   └── assets/
│       ├── starter-templates/
│       │   ├── flux2_klein.json              # Workflow JSON (from cenobite-agent)
│       │   ├── flux2_klein.meta.json         # Template metadata
│       │   ├── flux2_klein_ultrawide.json
│       │   └── flux2_klein_ultrawide.meta.json
│       ├── claude-commands/
│       │   └── generate.md                   # /generate slash command for art projects
│       ├── claude-md-template.md             # CLAUDE.md template for art project folders
│       └── mcp-json-template.json            # .mcp.json template with placeholders
│
├── templates/                       # Dev-time templates (used when running from repo directly)
│   ├── flux2_klein.json
│   ├── flux2_klein.meta.json
│   ├── flux2_klein_ultrawide.json
│   └── flux2_klein_ultrawide.meta.json
│
├── tests/
│   ├── conftest.py                  # Shared fixtures: respx mocks, sample template data
│   ├── test_server.py               # MCP tool integration tests
│   ├── test_comfyui.py              # ComfyUI client tests (HTTP mocking)
│   ├── test_templates.py            # Template CRUD + validation tests
│   ├── test_errors.py               # Error helper tests
│   └── test_init.py                 # Init command tests (filesystem scaffolding)
│
└── output/                          # Dev-time output (gitignored)
    └── .gitkeep
```

### Architectural Boundaries

**MCP Protocol Boundary** (`server.py`):
- FastMCP handles all JSON-RPC protocol details
- `server.py` is the only module that imports FastMCP and registers tools
- Tool functions in `server.py` orchestrate calls to `templates.py` and `comfyui.py`
- All tools return `dict` — server.py never exposes internal types to MCP

**ComfyUI HTTP Boundary** (`comfyui.py`):
- Only module that makes HTTP calls to ComfyUI
- Encapsulates `httpx.AsyncClient` usage
- Translates ComfyUI API responses into internal dicts
- Handles connection errors → structured `transient_error()` responses
- Three endpoints: `POST /prompt`, `GET /history/{id}`, `GET /view`

**Template Filesystem Boundary** (`templates.py`):
- Only module that reads/writes template files
- Handles path sanitization (no `/`, `..`, leading `.` in template names)
- Validates `.meta.json` structure on write
- Returns template data as plain dicts — no leaking file handles or paths

**Init Boundary** (`init.py`):
- Only module that writes to the target project folder (outside the repo)
- Copies assets from `slop_studio/assets/` to target directory
- Generates `.mcp.json` with repo path interpolated
- Never touches the server or ComfyUI — completely independent

### Data Flow

```
Claude Code → (stdio/MCP) → server.py → templates.py → filesystem (templates/)
                                       → comfyui.py  → HTTP → ComfyUI instance
                                       → comfyui.py  → filesystem (output/)
                                       → errors.py   → (error construction only)
```

### Integration Points

**Internal:** `server.py` imports and calls `templates.py`, `comfyui.py`, `errors.py`, `config.py`. No other cross-module imports. `templates.py` and `comfyui.py` do not import each other.

**External:** Single integration point — ComfyUI HTTP API via `comfyui.py`. No other external services.

## Architecture Validation Results

### Coherence Validation ✅

**Decision Compatibility:** All technology choices are compatible. FastMCP 3.1.1 + httpx 0.28.1 + Python 3.11+ have no version conflicts. Async architecture aligns across all layers (FastMCP async tools, httpx.AsyncClient, asyncio.sleep for polling). Package layout supports both MVP (uv run) and Phase 2 (uvx) distribution.

**Pattern Consistency:** PEP 8 naming applied uniformly across code, MCP tool names, and JSON responses. Tool return format (dict with status field) is consistent between success and error paths. Error helpers produce the same shape as manual dicts would, enforced by dataclass→asdict conversion.

**Structure Alignment:** Module boundaries enforce one-directional dependency flow from server.py. No circular imports possible. Init command is fully decoupled from server modules. Each module owns a single external concern (MCP protocol, HTTP, filesystem, or project scaffolding).

### Requirements Coverage ✅

**Functional Requirements:** All 30 FRs are architecturally supported. FR7 (schema validation) is intentionally scoped to meta-only for MVP with cross-reference validation deferred to Phase 2.

**Non-Functional Requirements:** HTTP timeout (30s), polling strategy (3s/45s), fail-fast startup, stderr logging, and synchronous file I/O are all addressed in the architecture.

**Architectural Addition:** The init command (`slop_studio/init.py`) and project scaffolding (`.mcp.json`, slash commands, CLAUDE.md) were added during architecture to support the multi-project-folder workflow. This is additive to the PRD — no PRD requirements were removed or modified.

### Implementation Readiness ✅

**Decision Completeness:** All critical decisions documented with specific versions. Implementation patterns include code examples. Error taxonomy fully enumerated with helper function signatures.

**Structure Completeness:** Complete project tree with every file named and purpose documented. Module boundaries, data flow, and integration points explicitly defined.

**Pattern Completeness:** All 7 identified conflict points addressed with concrete patterns and enforcement guidelines.

### Gap Analysis Results

**Critical Gaps:** None found.

**Important Gaps:**
- The PRD should be updated to include the init command as a new FR, since it emerged as an architectural feature
- Slash command content (e.g., `/generate`) needs to be designed during story creation — the architecture defines where it lives but not what it does

**Nice-to-Have:**
- `fastmcp dev` (Inspector) workflow could be documented for developer experience
- A `.env.example` file could be added to the project structure for documentation

### Architecture Completeness Checklist

**✅ Requirements Analysis**
- [x] Project context thoroughly analyzed
- [x] Scale and complexity assessed (low)
- [x] Technical constraints identified (ComfyUI API, stdio transport)
- [x] Cross-cutting concerns mapped (errors, path sanitization, seeds, validation)

**✅ Architectural Decisions**
- [x] Critical decisions documented with versions
- [x] Technology stack fully specified (FastMCP 3.1.1, httpx 0.28.1, Python 3.11+)
- [x] Integration patterns defined (single HTTP boundary to ComfyUI)
- [x] Performance considerations addressed (polling, timeouts, no caching needed)

**✅ Implementation Patterns**
- [x] Naming conventions established (PEP 8 throughout)
- [x] Structure patterns defined (dict returns, error helpers)
- [x] Communication patterns specified (tool docstrings, logging)
- [x] Process patterns documented (error construction, config access)

**✅ Project Structure**
- [x] Complete directory structure defined
- [x] Component boundaries established (4 boundaries: MCP, HTTP, filesystem, init)
- [x] Integration points mapped (single external: ComfyUI HTTP API)
- [x] Requirements to structure mapping complete (all 30 FRs mapped)

### Architecture Readiness Assessment

**Overall Status:** READY FOR IMPLEMENTATION

**Confidence Level:** High — low-complexity project with well-defined boundaries, proven source logic (cenobite-agent), and a single external dependency.

**Key Strengths:**
- Clean module boundaries with one-directional dependency flow
- Consistent error handling pattern enforced by helpers
- Init command enables the multi-project workflow naturally
- Clear Phase 2 migration path (package layout → PyPI → uvx)

**Areas for Future Enhancement:**
- Cross-reference template validation (Phase 2)
- PyPI distribution via uvx (Phase 2)
- Persistent httpx.AsyncClient via FastMCP lifespan (Phase 2)
- Additional slash commands and CLAUDE.md refinements based on usage

### Implementation Handoff

**AI Agent Guidelines:**
- Follow all architectural decisions exactly as documented
- Use implementation patterns consistently across all components
- Respect module boundaries — server.py orchestrates, other modules own their domain
- Use error helpers exclusively — never construct error dicts manually
- Refer to this document for all architectural questions

**First Implementation Priority:**
1. Set up package structure (`slop_studio/` with `__init__.py`, `config.py`, `errors.py`)
2. Add dependencies (`uv add fastmcp httpx`)
3. Implement `server.py` with FastMCP instance and first tool (`list_templates`)
