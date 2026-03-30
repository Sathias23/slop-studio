---
stepsCompleted:
  - "step-01-validate-prerequisites"
  - "step-02-design-epics"
  - "step-03-create-stories"
  - "step-04-final-validation"
status: "complete"
completedAt: "2026-03-29"
inputDocuments:
  - "prd.md"
  - "architecture.md"
---

# ComfyClaude - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for ComfyClaude, decomposing the requirements from the PRD and Architecture into implementable stories.

## Requirements Inventory

### Functional Requirements

FR1: Claude Code can list all available workflow templates with summary metadata (name, model, description, supported aspect ratios, expected duration)
FR2: Claude Code can inspect a specific template's full metadata including required inputs, optional inputs with defaults, and supported aspect ratios
FR3: Claude Code can determine the appropriate template for a user's intent based on rich tool descriptions and template metadata
FR4: Claude Code can add a new workflow template by providing workflow JSON and metadata
FR5: Claude Code can update an existing workflow template's workflow JSON and/or metadata
FR6: Claude Code can delete a workflow template by name
FR7: The server validates template schema on write (required meta fields present, input node IDs reference nodes in workflow, resolution nodes valid)
FR8: The server rejects template names containing path traversal characters (`/`, `..`, leading `.`)
FR9: Claude Code can submit a generation job by specifying template name, input values, and optional aspect ratio
FR10: The server injects user-provided input values into the correct workflow nodes based on template input definitions
FR11: The server randomizes seed fields across all workflow nodes on each submission to prevent ComfyUI cache hits
FR12: The server maps aspect ratio labels to width/height dimensions and injects them into template-defined resolution nodes
FR13: The server submits the prepared workflow to ComfyUI's `/prompt` API and returns a prompt ID
FR14: Claude Code can poll a job for completion status with configurable wait duration
FR15: The server returns structured job status: pending, running, completed, or failed with error details
FR16: The server polls ComfyUI's history API at a default 3-second interval, capped at 45 seconds
FR17: The server returns early when job completion is detected before the timeout cap
FR18: Claude Code can perform a single non-blocking status check (zero wait)
FR19: Claude Code can retrieve the absolute file path of a completed generation's output image
FR20: The server organizes output images by date under a configurable output directory (`{output_dir}/{YYYY-MM-DD}/{filename}`)
FR21: The server sanitizes image filenames via `os.path.basename()` before any file operations
FR22: The server returns structured error responses with fields: status, error message, error type, and retry suggestion
FR23: The server classifies errors as transient (unreachable, generation_failed, storage_error) with `retry_suggested: true`
FR24: The server classifies errors as terminal (invalid_inputs, invalid_workflow, model_not_found, directory_not_found, permission_denied, completed_no_output) with `retry_suggested: false`
FR25: Error messages include enough context for Claude Code to explain the problem and suggest corrective action without user debugging
FR26: The server reads ComfyUI base URL from `COMFYUI_URL` environment variable, defaulting to `http://localhost:8188`
FR27: The server reads template directory from `COMFYCLAUDE_TEMPLATES_DIR` environment variable, defaulting to `./templates`
FR28: The server reads output directory from `COMFYCLAUDE_OUTPUT_DIR` environment variable, defaulting to `./output`
FR29: The server ships with two starter templates (`flux2_klein`, `flux2_klein_ultrawide`) that work immediately with a standard ComfyUI installation
FR30: The server operates over stdio transport for direct integration with Claude Code's MCP configuration

### NonFunctional Requirements

NFR1: HTTP request timeout: 30 seconds per httpx call to ComfyUI API
NFR2: Job polling overhead: negligible — 3-second sleep intervals with lightweight GET requests; poll cap: 45 seconds with early return
NFR3: Fail-fast startup if ComfyUI is unreachable — do not start MCP server if ComfyUI instance cannot be reached; transient failures during operation return structured errors (server does not crash)
NFR4: No caching required — filesystem scan per call is sufficient for single-user use; synchronous file I/O for image operations

### Additional Requirements

- Starter template: `uv init` (application) + FastMCP conventions already selected. Project initialized with `uv init`.
- Package layout with `slop_studio/` package, `main.py` entry point supporting "serve" or "init" subcommands
- Init command (`slop_studio/init.py`): scaffold art project folders, copy starter templates, generate `.mcp.json` with repo path, copy slash commands and CLAUDE.md template
- Async architecture throughout: `async def` tools, `httpx.AsyncClient`, `asyncio.sleep` for polling
- Dataclass-based `ErrorResponse` with `transient_error()` / `terminal_error()` helper functions — never construct error dicts manually
- Meta-only template validation for MVP (cross-reference validation deferred to Phase 2)
- Testing with `pytest` + `respx` for httpx mock responses; all ComfyUI HTTP calls mocked
- Logging to stderr via Python `logging` module (stdout reserved for MCP stdio protocol)

### UX Design Requirements

N/A — No UX Design document exists for this project (developer tool with no UI).

### FR Coverage Map

FR1: Epic 2 - List templates with summary metadata
FR2: Epic 2 - Inspect template full metadata
FR3: Epic 2 - Template selection from rich descriptions
FR4: Epic 3 - Add new workflow template
FR5: Epic 3 - Update existing template
FR6: Epic 3 - Delete template by name
FR7: Epic 3 - Schema validation on write
FR8: Epic 3 - Path traversal rejection
FR9: Epic 2 - Submit generation job
FR10: Epic 2 - Input injection into workflow nodes
FR11: Epic 2 - Seed randomization across all nodes
FR12: Epic 2 - Aspect ratio mapping to dimensions
FR13: Epic 2 - Submit workflow to ComfyUI `/prompt` API
FR14: Epic 2 - Poll job with configurable wait
FR15: Epic 2 - Structured job status return
FR16: Epic 2 - 3s poll interval, 45s cap
FR17: Epic 2 - Early return on completion
FR18: Epic 2 - Non-blocking single status check
FR19: Epic 2 - Retrieve absolute image file path
FR20: Epic 2 - Date-organized output directory
FR21: Epic 2 - Filename sanitization
FR22: Epic 1 - Structured error responses
FR23: Epic 1 - Transient error classification
FR24: Epic 1 - Terminal error classification
FR25: Epic 1 - Context-rich error messages
FR26: Epic 1 - COMFYUI_URL env var config
FR27: Epic 1 - COMFYCLAUDE_TEMPLATES_DIR env var config
FR28: Epic 1 - COMFYCLAUDE_OUTPUT_DIR env var config
FR29: Epic 2 - Two starter templates ship out of box
FR30: Epic 1 - stdio transport

## Epic List

### Epic 1: Core Server & First Connection
Developer can start the ComfyClaude MCP server over stdio, have it connect to their local ComfyUI instance, and receive structured error responses when things go wrong. This establishes the foundation every other epic builds on.
**FRs covered:** FR22, FR23, FR24, FR25, FR26, FR27, FR28, FR30

### Epic 2: Image Generation End-to-End
Developer can say "make me a picture" and get an image back — the complete happy path. Template discovery, job submission with input injection and seed randomization, completion polling, and image retrieval all work with the two starter templates out of the box.
**FRs covered:** FR1, FR2, FR3, FR9, FR10, FR11, FR12, FR13, FR14, FR15, FR16, FR17, FR18, FR19, FR20, FR21, FR29

### Epic 3: Template Management
Developer can expand beyond the starter templates by adding, updating, and deleting their own workflow templates with schema validation and path safety, enabling any ComfyUI workflow to be used conversationally.
**FRs covered:** FR4, FR5, FR6, FR7, FR8

### Epic 4: Project Onboarding & Init Command
Developer can run a single init command to scaffold a new art project folder with starter templates, `.mcp.json` configuration, slash commands, and a CLAUDE.md template — ready to generate images immediately.
**FRs covered:** Architecture init command requirements (no PRD FR — noted as gap in Architecture validation)

## Epic 1: Core Server & First Connection

Developer can start the ComfyClaude MCP server over stdio, have it connect to their local ComfyUI instance, and receive structured error responses when things go wrong.

### Story 1.1: Package Structure, Configuration & Error Types

As a developer building on ComfyClaude,
I want the project to have a properly structured Python package with typed configuration and error handling,
So that all future tools have consistent error responses and configurable behavior from day one.

**Acceptance Criteria:**

**Given** the repository has been cloned and `uv` is available
**When** I run `uv sync`
**Then** `fastmcp` and `httpx` are installed as dependencies, and `pytest` and `respx` as dev dependencies

**Given** the `slop_studio/` package exists
**When** I inspect the module structure
**Then** `__init__.py`, `config.py`, and `errors.py` are present

**Given** no environment variables are set
**When** `config.py` is imported
**Then** `COMFYUI_URL` defaults to `http://localhost:8188`, `TEMPLATES_DIR` defaults to `./templates`, and `OUTPUT_DIR` defaults to `./output` (FR26, FR27, FR28)

**Given** environment variables `COMFYUI_URL`, `COMFYCLAUDE_TEMPLATES_DIR`, and `COMFYCLAUDE_OUTPUT_DIR` are set
**When** `config.py` is imported
**Then** the constants reflect the environment variable values

**Given** `errors.py` is imported
**When** I call `transient_error("unreachable", "Cannot connect to ComfyUI")`
**Then** it returns `{"status": "error", "error": "Cannot connect to ComfyUI", "error_type": "unreachable", "retry_suggested": True}` (FR22, FR23)

**Given** `errors.py` is imported
**When** I call `terminal_error("invalid_workflow", "Node type not found")`
**Then** it returns `{"status": "error", "error": "Node type not found", "error_type": "invalid_workflow", "retry_suggested": False}` (FR22, FR24)

**Given** the error helpers exist
**When** any error response is constructed
**Then** it always contains `status`, `error`, `error_type`, and `retry_suggested` fields with context-rich messages (FR25)

**Given** the story is complete
**When** I run `uv run pytest tests/test_errors.py`
**Then** all error helper tests pass with respx/pytest

### Story 1.2: FastMCP Server with stdio Transport & Startup Validation

As a developer using Claude Code,
I want to start the ComfyClaude MCP server and have it validate that ComfyUI is reachable,
So that I get immediate feedback if my setup is broken rather than discovering it on the first tool call.

**Acceptance Criteria:**

**Given** `slop_studio/server.py` exists
**When** I inspect it
**Then** it creates a `FastMCP` server instance with a descriptive name and registers it for stdio transport (FR30)

**Given** `main.py` exists as the entry point
**When** I run `uv run main.py`
**Then** the server starts in stdio mode, ready to receive MCP tool calls

**Given** ComfyUI is running at the configured `COMFYUI_URL`
**When** the server starts
**Then** it successfully validates connectivity and begins accepting MCP requests (NFR3)

**Given** ComfyUI is NOT running at the configured `COMFYUI_URL`
**When** the server starts
**Then** it fails fast with a clear error message indicating ComfyUI is unreachable, and does not start the MCP server (NFR3)

**Given** the HTTP timeout is configured
**When** any HTTP request is made to ComfyUI during startup validation
**Then** the request uses a 30-second timeout (NFR1)

**Given** the server is running
**When** the MCP client sends an initialize handshake
**Then** the server responds successfully with an empty tools list (tool registration is completed in Epic 2 stories)

**Given** the repository root
**When** I inspect `README.md`
**Then** it contains: setup instructions (clone, uv sync, set COMFYUI_URL, add MCP config snippet), and the known-working ComfyUI version the server was tested against (NFR8)

**Given** the story is complete
**When** I run `uv run pytest tests/test_server.py`
**Then** all server startup and connectivity tests pass with mocked HTTP via respx

## Epic 2: Image Generation End-to-End

Developer can say "make me a picture" and get an image back — the complete happy path. Template discovery, job submission with input injection and seed randomization, completion polling, and image retrieval all work with the two starter templates out of the box.

### Story 2.1: Template Discovery & Starter Templates

As a developer using Claude Code,
I want to browse available workflow templates and inspect their metadata,
So that Claude Code can select the right template for my intent without me having to know template details.

**Acceptance Criteria:**

**Given** the `templates/` directory contains starter templates
**When** I call the `list_templates` MCP tool
**Then** it returns a list of all templates with summary metadata: name, model, description, supported aspect ratios, and expected duration (FR1)

**Given** a template named `flux2_klein` exists
**When** I call the `get_template` MCP tool with `template_name: "flux2_klein"`
**Then** it returns the full metadata including required inputs, optional inputs with defaults, and supported aspect ratios (FR2)

**Given** the tool descriptions are registered in FastMCP
**When** Claude Code reads the tool descriptions
**Then** they contain enough context for Claude Code to determine the appropriate template for a user's intent (FR3)

**Given** the project is freshly cloned
**When** I inspect the `templates/` directory
**Then** it contains `flux2_klein.json`, `flux2_klein.meta.json`, `flux2_klein_ultrawide.json`, and `flux2_klein_ultrawide.meta.json` (FR29)

**Given** a template name does not exist
**When** I call `get_template` with that name
**Then** it returns a terminal error with `error_type: "invalid_inputs"` and a clear message

**Given** the story is complete
**When** I run `uv run pytest tests/test_templates.py`
**Then** all template discovery tests pass

### Story 2.2: Job Submission with Input Injection & Seed Randomization

As a developer using Claude Code,
I want to submit an image generation job by specifying a template and my inputs,
So that ComfyUI generates an image based on my request without me touching the workflow JSON.

**Acceptance Criteria:**

**Given** a valid template name and input values (e.g., prompt text)
**When** I call the `queue_prompt` MCP tool with `template_name`, `inputs`, and optional `aspect_ratio`
**Then** the server loads the template, injects inputs, and submits to ComfyUI's `/prompt` API, returning a `prompt_id` (FR9, FR13)

**Given** the template defines input nodes (e.g., node "6" is a text prompt)
**When** the server prepares the workflow
**Then** user-provided input values are injected into the correct workflow nodes based on template input definitions (FR10)

**Given** the workflow contains seed or noise_seed fields across multiple nodes
**When** the server prepares the workflow for submission
**Then** all seed fields are randomized to prevent ComfyUI cache hits (FR11)

**Given** the user specifies `aspect_ratio: "16:9"` and the template defines resolution nodes and aspect ratio mappings
**When** the server prepares the workflow
**Then** it maps the aspect ratio label to width/height dimensions and injects them into the template-defined resolution nodes (FR12)

**Given** no aspect ratio is specified
**When** the server prepares the workflow
**Then** it uses the template's default resolution

**Given** ComfyUI is unreachable when submitting
**When** the `queue_prompt` tool is called
**Then** it returns a transient error with `error_type: "unreachable"` and `retry_suggested: true`

**Given** the template name doesn't exist
**When** `queue_prompt` is called
**Then** it returns a terminal error with `error_type: "invalid_inputs"`

**Given** the story is complete
**When** I run `uv run pytest tests/test_comfyui.py`
**Then** all job submission tests pass with mocked HTTP via respx

### Story 2.3: Job Monitoring & Completion Polling

As a developer using Claude Code,
I want to poll a submitted job for completion,
So that I know when my image is ready without manually checking ComfyUI.

**Acceptance Criteria:**

**Given** a valid `prompt_id` from a submitted job
**When** I call the `check_job` MCP tool with `prompt_id` and optional `wait` duration
**Then** it returns structured status: `pending`, `running`, `completed`, or `failed` with error details (FR14, FR15)

**Given** a job is still processing and `wait` is specified (e.g., `wait: 30`)
**When** the server polls ComfyUI's `/history/{id}` API
**Then** it polls at 3-second intervals, capped at 45 seconds maximum (FR16)

**Given** a job completes before the timeout cap
**When** the server is polling
**Then** it returns immediately with `status: "completed"` and output details without waiting for the remaining timeout (FR17)

**Given** a `prompt_id` and `wait: 0` (or no wait parameter)
**When** I call `check_job`
**Then** it performs a single non-blocking status check and returns immediately (FR18)

**Given** the poll timeout is reached and the job is still running
**When** the server returns
**Then** it returns `status: "running"` so Claude Code knows to poll again

**Given** the job failed in ComfyUI
**When** `check_job` detects the failure
**Then** it returns `status: "failed"` with a descriptive error message and appropriate error type

**Given** the story is complete
**When** I run `uv run pytest tests/test_comfyui.py`
**Then** all polling and job monitoring tests pass with mocked HTTP

### Story 2.4: Image Retrieval & Output Organization

As a developer using Claude Code,
I want to retrieve the file path of my generated image,
So that I can view, use, or reference it directly from the terminal.

**Acceptance Criteria:**

**Given** a completed job with a `prompt_id`
**When** I call the `get_image` MCP tool with `prompt_id`
**Then** it returns the absolute file path to the output image (FR19)

**Given** the `get_image` tool retrieves an image from ComfyUI
**When** the server saves the image
**Then** it organizes it under `{output_dir}/{YYYY-MM-DD}/{filename}` (FR20)

**Given** the output date directory does not yet exist
**When** the server saves an image
**Then** it creates the date directory automatically

**Given** any image filename from ComfyUI
**When** the server processes the filename
**Then** it sanitizes it via `os.path.basename()` before any file operations to prevent path traversal (FR21)

**Given** a `prompt_id` for a job that hasn't completed
**When** I call `get_image`
**Then** it returns an appropriate error indicating the job isn't complete yet

**Given** a `prompt_id` for a completed job that produced no output
**When** I call `get_image`
**Then** it returns a terminal error with `error_type: "completed_no_output"` (FR24)

**Given** the output directory is not writable
**When** the server attempts to save an image
**Then** it returns a transient error with `error_type: "storage_error"` (FR23)

**Given** the story is complete
**When** I run `uv run pytest tests/test_comfyui.py`
**Then** all image retrieval and output organization tests pass

## Epic 3: Template Management

Developer can expand beyond the starter templates by adding, updating, and deleting their own workflow templates with schema validation and path safety, enabling any ComfyUI workflow to be used conversationally.

### Story 3.1: Add & Update Workflow Templates with Validation

As a developer using Claude Code,
I want to add new workflow templates and update existing ones from exported ComfyUI workflows,
So that I can use any ComfyUI workflow conversationally without manually editing JSON files.

**Acceptance Criteria:**

**Given** a valid workflow JSON and metadata (model, description, inputs, aspect ratios, duration)
**When** I call the `add_template` MCP tool with `name`, `workflow_json`, and `metadata`
**Then** it saves `{name}.json` and `{name}.meta.json` to the templates directory (FR4)

**Given** an existing template named `sdxl_turbo`
**When** I call the `update_template` MCP tool with `name: "sdxl_turbo"` and new workflow JSON and/or metadata
**Then** it overwrites the existing `.json` and/or `.meta.json` files (FR5)

**Given** a template name containing path traversal characters (`/`, `..`, or leading `.`)
**When** I call `add_template` or `update_template` with that name
**Then** it returns a terminal error with `error_type: "invalid_inputs"` and rejects the operation (FR8)

**Given** metadata is missing required fields (e.g., no input definitions, no model name)
**When** I call `add_template` or `update_template`
**Then** it returns a terminal error with `error_type: "invalid_inputs"` describing which fields are missing (FR7)

**Given** metadata defines input node IDs that are structurally invalid or resolution nodes that are malformed
**When** I call `add_template` or `update_template`
**Then** it validates the meta structure and returns a terminal error if validation fails (FR7 — meta-only validation per Architecture)

**Given** a template was just added via `add_template`
**When** I call `list_templates`
**Then** the new template appears immediately in the listing

**Given** the story is complete
**When** I run `uv run pytest tests/test_templates.py`
**Then** all add/update and validation tests pass

### Story 3.2: Delete Workflow Templates

As a developer using Claude Code,
I want to remove workflow templates I no longer need,
So that my template list stays clean and Claude Code doesn't suggest outdated workflows.

**Acceptance Criteria:**

**Given** an existing template named `sdxl_turbo`
**When** I call the `delete_template` MCP tool with `name: "sdxl_turbo"`
**Then** both `sdxl_turbo.json` and `sdxl_turbo.meta.json` are removed from the templates directory (FR6)

**Given** a template name that doesn't exist
**When** I call `delete_template` with that name
**Then** it returns a terminal error with `error_type: "invalid_inputs"` and a clear message

**Given** a template name containing path traversal characters
**When** I call `delete_template` with that name
**Then** it returns a terminal error with `error_type: "invalid_inputs"` (FR8)

**Given** a template was just deleted
**When** I call `list_templates`
**Then** the deleted template no longer appears

**Given** the story is complete
**When** I run `uv run pytest tests/test_templates.py`
**Then** all delete tests pass

## Epic 4: Project Onboarding & Init Command

Developer can run a single init command to scaffold a new art project folder with starter templates, `.mcp.json` configuration, slash commands, and a CLAUDE.md template — ready to generate images immediately.

### Story 4.1: Init Command Scaffolds Art Project

As a developer starting a new art project,
I want to run a single init command that sets up everything I need,
So that I can start generating images in Claude Code immediately without manual configuration.

**Acceptance Criteria:**

**Given** `main.py` supports subcommands
**When** I run `uv run --directory ~/Projects/ComfyClaude init` from any directory
**Then** it scaffolds the current working directory as an art project

**Given** the init command runs successfully
**When** I inspect the scaffolded directory
**Then** it contains:
- A `templates/` folder with the two starter templates (`flux2_klein` and `flux2_klein_ultrawide` — both `.json` and `.meta.json`)
- A `.mcp.json` file configured with `uv run --directory` pointing back to the ComfyClaude repo
- A `.claude/commands/` folder with the `generate.md` slash command
- A `CLAUDE.md` file from the template

**Given** the `.mcp.json` is generated
**When** I inspect it
**Then** the `command` field uses `uv run --directory {absolute_path_to_comfyclaude_repo}` so it works from any project folder

**Given** the target directory already has a `.mcp.json` or `CLAUDE.md`
**When** I run the init command
**Then** it warns before overwriting or skips existing files (does not silently destroy user config)

**Given** the init command copies starter templates
**When** I inspect the copied files
**Then** they are identical to the originals in `slop_studio/assets/starter-templates/`

**Given** the init command completes
**When** I open Claude Code in the scaffolded directory
**Then** ComfyClaude MCP tools are available and the `/generate` slash command is usable

**Given** the story is complete
**When** I run `uv run pytest tests/test_init.py`
**Then** all init scaffolding tests pass (filesystem assertions, no real ComfyUI needed)
