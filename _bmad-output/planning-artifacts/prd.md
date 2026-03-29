---
stepsCompleted:
  - "step-01-init"
  - "step-02-discovery"
  - "step-02b-vision"
  - "step-02c-executive-summary"
  - "step-03-success"
  - "step-04-journeys"
  - "step-05-domain"
  - "step-06-innovation"
  - "step-07-project-type"
  - "step-08-scoping"
  - "step-09-functional"
  - "step-10-nonfunctional"
  - "step-11-polish"
inputDocuments:
  - "product-brief-ComfyClaude.md"
  - "product-brief-ComfyClaude-distillate.md"
documentCounts:
  briefs: 2
  research: 0
  brainstorming: 0
  projectDocs: 0
classification:
  projectType: developer_tool
  domain: general
  complexity: low
  projectContext: greenfield
workflowType: 'prd'
---

# Product Requirements Document - ComfyClaude

**Author:** Brad
**Date:** 2026-03-29

## Executive Summary

ComfyClaude is a Python MCP server that gives Claude Code native tools to interact with a local ComfyUI instance. It extracts battle-tested image generation patterns from cenobite-agent — template management, input injection, completion polling, structured error handling — and repackages them as eight MCP tools across five focused categories over stdio transport. The result: say "make a picture" in Claude Code and get an image back without leaving the terminal, at zero additional inference cost beyond an existing Claude subscription.

The primary user is a developer running ComfyUI locally on macOS with Apple Silicon who wants conversational image generation — creative exploration, project asset generation, visual iteration — integrated into their existing Claude Code workflow rather than routed through a separate agent platform with per-call inference costs.

### What Makes This Special

The ComfyUI MCP landscape has 15+ servers with no clear winner. They split between kitchen-sink solutions (40+ tools, cloud-first, complex setup) and minimal wrappers (no templates, no polling, fire-and-forget). ComfyClaude occupies the gap: five tool categories (eight tools total), template-driven workflow selection with structured metadata sidecars, built-in completion polling, and a local-first design that works out of the box with starter templates. The core logic is already proven in production via cenobite-agent — this is a repackaging into the right delivery mechanism, not a greenfield experiment.

## Project Classification

- **Project Type:** Developer tool (MCP server / integration library)
- **Domain:** General (creative tooling / developer productivity)
- **Complexity:** Low (single-user, local-first, no regulatory concerns)
- **Project Context:** Greenfield (new standalone project extracting from existing codebase)

## Success Criteria

### User Success

- "Make a picture" works in a single conversational exchange: list templates, select, queue, poll, return image path — no context switching to a browser
- Template CRUD is reliable: add a new workflow template, use it immediately in the next generation
- Error messages are specific and typed (transient vs terminal) so Claude Code can self-correct without user intervention
- First image generated within 5 minutes of setup: clone, set `COMFYUI_URL`, add MCP config, generate

### Business Success

- Replaces cenobite-agent as the primary path for conversational image generation, eliminating per-call inference costs
- Used regularly for creative exploration and project asset generation within Claude Code workflows
- Starter templates provide a working out-of-box experience with zero template authoring required

### Technical Success

- Eight tools across five categories over stdio transport
- Job polling returns correct status across all states (pending, running, completed, failed) and handles timeouts gracefully
- Template `.json` + `.meta.json` pairs are human-readable, git-trackable, and validated on write
- Structured error responses distinguish transient failures (retry) from terminal ones (fix required)

### Measurable Outcomes

- Setup-to-first-image: under 5 minutes
- Single-exchange generation: list → select → queue → poll → path in one conversation turn
- Template CRUD round-trip: add template, generate from it immediately
- Error self-correction: Claude Code recovers from transient errors without user prompting

## User Journeys

### Journey 1: First Image Generation (Happy Path)

**Brad** has just discovered ComfyClaude. He's been using cenobite-agent for conversational image generation but the inference costs are adding up. He clones the repo, sets `COMFYUI_URL` to his local ComfyUI instance, and adds the MCP config snippet to Claude Code. Total setup: 3 minutes.

He opens Claude Code and types: "make me a cyberpunk cityscape at sunset." Claude Code calls `list_templates`, sees the flux2_klein starter template with its metadata — model, supported aspect ratios, expected duration, required inputs. It selects the template, crafts a detailed technical prompt from Brad's natural language, picks 16:9 for the cinematic feel, and calls `queue_prompt`. The job is submitted.

Claude Code calls `check_job`, polling every 3 seconds. Thirty seconds later, the job completes. Claude Code calls `get_image` and returns the absolute file path: `~/comfyclaude-output/2026-03-29/ComfyUI_00042_.png`. Brad opens it. It's exactly the kind of thing he used to do through cenobite-agent — but this time it's covered by his Claude subscription.

**The moment:** "That just worked. Same experience, no extra inference bill."

### Journey 2: Adding a New Workflow Template

**Brad** has been using flux2_klein for a few weeks but wants to try a different model — he's set up an SDXL workflow in ComfyUI's browser UI and exported the workflow JSON. He tells Claude Code: "Add this as a new template called sdxl_turbo" and pastes or references the exported JSON.

Claude Code calls `add_template`, passing the workflow JSON and metadata (model name, expected duration, input definitions, supported aspect ratios). The server validates the schema — checks that required meta fields are present, input node IDs reference real nodes in the workflow, resolution nodes are valid. The template is saved as `sdxl_turbo.json` + `sdxl_turbo.meta.json`.

Brad immediately says "generate a portrait using the new sdxl_turbo template." Claude Code calls `list_templates`, sees sdxl_turbo in the registry with its metadata, selects it, and queues the job. It works on the first try.

**The moment:** "New workflow available instantly, no manual JSON editing, validated on save."

### Journey 3: Error Recovery — ComfyUI Down

**Brad** asks for an image but ComfyUI isn't running — he forgot to start it after a reboot. Claude Code calls `queue_prompt`, and the server returns a structured error: `{status: "error", error: "Cannot connect to ComfyUI at http://localhost:8188", error_type: "unreachable", retry_suggested: true}`.

Claude Code reads the error type, recognizes it's transient, and tells Brad: "ComfyUI doesn't seem to be running. Can you start it and I'll try again?" Brad starts ComfyUI, tells Claude Code to retry. This time it connects and the job queues successfully.

**The moment:** No cryptic stack trace, no debugging. The error told Claude Code exactly what happened and what to do about it.

### Journey 4: Error Recovery — Broken Template

**Brad** updated some custom nodes in ComfyUI and now his sdxl_turbo template references a node that no longer exists. He asks for an image using that template. The server submits the job, but ComfyUI rejects it. The server returns: `{status: "error", error: "Node type 'KSamplerAdvanced_v2' not found", error_type: "invalid_workflow", retry_suggested: false}`.

Claude Code recognizes this is terminal — retrying won't help. It tells Brad: "The sdxl_turbo template references a node type that's no longer installed in ComfyUI. You may need to update the template with the new node configuration." Brad re-exports the workflow from ComfyUI's browser UI and uses `update_template` to fix it.

**The moment:** The error pinpoints the problem. Brad knows exactly what to fix and has the tool to fix it.

### Journey Requirements Summary

| Capability | Revealed By |
|---|---|
| Template discovery with rich metadata | Journey 1 (Claude Code needs to select the right template) |
| Job submission with input injection & aspect ratio | Journey 1, 2 (core generation flow) |
| Completion polling with structured status | Journey 1 (wait and return result) |
| Image path retrieval organized by date | Journey 1 (file-based, persistent) |
| Template CRUD with schema validation | Journey 2 (add/update validated on write) |
| Structured error responses with error types | Journey 3, 4 (self-correction by Claude Code) |
| Transient vs terminal error classification | Journey 3 vs 4 (retry vs fix) |
| Seed randomization across all nodes | Journey 1 (cache busting, implicit) |
| Path sanitization | All journeys (security, implicit) |

## Developer Tool Specific Requirements

### Project-Type Overview

ComfyClaude is a Python MCP server consumed exclusively by Claude Code over stdio transport. It is not a general-purpose library, SDK, or CLI — it is a single-purpose integration that exposes ComfyUI capabilities as MCP tools. Installation is clone-and-run with `uv`, no package registry publication for v1.

### Technical Architecture Considerations

- **Runtime:** Python 3.11+, managed by `uv`
- **Framework:** FastMCP (Python) for MCP server implementation
- **Transport:** stdio (single-user, local process communication)
- **HTTP client:** httpx for ComfyUI API communication
- **Configuration:** Environment variables (`COMFYUI_URL`, `COMFYCLAUDE_TEMPLATES_DIR`, `COMFYCLAUDE_OUTPUT_DIR`)
- **Template storage:** Filesystem-based `.json` + `.meta.json` pairs, no database

### API Surface (MCP Tools)

Five categories, eight tools:

| Tool | Category | Purpose | Key Parameters |
|---|---|---|---|
| `list_templates` | Discovery | Browse available workflow templates | None |
| `get_template` | Discovery | Inspect template metadata and inputs | `template_name` |
| `add_template` | Management | Register new workflow template with validation | `name`, `workflow_json`, `metadata` |
| `update_template` | Management | Update existing template | `name`, `workflow_json`, `metadata` |
| `delete_template` | Management | Remove template from registry | `name` |
| `queue_prompt` | Generation | Submit generation job | `template_name`, `inputs`, `aspect_ratio` (optional) |
| `check_job` | Monitoring | Poll job for completion | `prompt_id`, `wait` (optional) |
| `get_image` | Retrieval | Retrieve image file path | `prompt_id` or `filename` |

### Installation Method

1. Clone repository
2. Ensure Python 3.11+ available (via `uv`)
3. Set `COMFYUI_URL` environment variable (default: `http://localhost:8188`)
4. Add MCP config snippet to Claude Code settings
5. Two starter templates ship out of the box: `flux2_klein` and `flux2_klein_ultrawide` (migrated from cenobite-agent)

### Documentation

- README with setup instructions, MCP config snippet, and quick-start guide
- Rich MCP tool descriptions serve as inline documentation for Claude Code — tool descriptions must be detailed enough for Claude Code to select the right template and construct valid inputs without user guidance

## Project Scoping & Phased Development

### MVP Strategy & Philosophy

**MVP Approach:** Problem-solving MVP — deliver the core "say what you want, get an image" loop with minimal surface area. The product brief's "five tools, not forty" principle is the scope constraint.
**Resource Requirements:** Solo developer, clone-and-run deployment, no infrastructure.

### MVP Feature Set (Phase 1)

**Core User Journeys Supported:**
- Journey 1: First image generation (happy path end-to-end)
- Journey 2: Adding a new workflow template
- Journey 3 & 4: Error recovery (transient and terminal)

**Must-Have Capabilities:**
- 8 MCP tools: `list_templates`, `get_template`, `add_template`, `update_template`, `delete_template`, `queue_prompt`, `check_job`, `get_image`
- Template `.json` + `.meta.json` pair management with schema validation on write
- Input injection with seed randomization and aspect ratio mapping
- Completion polling (3s interval, 45s cap, early return)
- Structured error responses with transient/terminal classification
- Path sanitization on template names and image filenames
- Two starter templates: `flux2_klein` and `flux2_klein_ultrawide`
- Configuration via environment variables with sensible defaults
- Unit tests with mocked ComfyUI API

### Post-MVP Features

**Phase 2 (Growth):**
- Model management (`list_models`, `load_model`)
- Job cancellation
- Seed control for reproducibility
- Image filtering by template name
- Persistent httpx.AsyncClient via lifespan

**Phase 3 (Expansion):**
- Iterative generation loops (generate → pick → refine)
- Batch generation and automated asset pipelines
- Community-shareable template library
- WebSocket progress streaming
- SSE/HTTP transport for shared access

### Risk Mitigation Strategy

**Technical Risks:**
- *ComfyUI API instability:* Pin to known-working ComfyUI version, document compatibility. No abstraction layer — direct HTTP calls are easiest to update if endpoints change.
- *Template brittleness:* Clear error messages when templates reference missing nodes/models. No runtime validation against ComfyUI instance — keep it simple.

**Market Risks:** N/A — personal tool, not competing for market share.

**Resource Risks:** Solo project with small surface area. If time is tight, the two starter templates and core generation loop are the irreducible minimum — template CRUD can be manual (edit JSON files) as a last resort.

## Functional Requirements

### Template Discovery

- FR1: Claude Code can list all available workflow templates with summary metadata (name, model, description, supported aspect ratios, expected duration)
- FR2: Claude Code can inspect a specific template's full metadata including required inputs, optional inputs with defaults, and supported aspect ratios
- FR3: Claude Code can determine the appropriate template for a user's intent based on rich tool descriptions and template metadata

### Template Management

- FR4: Claude Code can add a new workflow template by providing workflow JSON and metadata
- FR5: Claude Code can update an existing workflow template's workflow JSON and/or metadata
- FR6: Claude Code can delete a workflow template by name
- FR7: The server validates template schema on write (required meta fields present, input node IDs reference nodes in workflow, resolution nodes valid)
- FR8: The server rejects template names containing path traversal characters (`/`, `..`, leading `.`)

### Image Generation

- FR9: Claude Code can submit a generation job by specifying template name, input values, and optional aspect ratio
- FR10: The server injects user-provided input values into the correct workflow nodes based on template input definitions
- FR11: The server randomizes seed fields across all workflow nodes on each submission to prevent ComfyUI cache hits
- FR12: The server maps aspect ratio labels to width/height dimensions and injects them into template-defined resolution nodes
- FR13: The server submits the prepared workflow to ComfyUI's `/prompt` API and returns a prompt ID

### Job Monitoring

- FR14: Claude Code can poll a job for completion status with configurable wait duration
- FR15: The server returns structured job status: pending, running, completed, or failed with error details
- FR16: The server polls ComfyUI's history API at a default 3-second interval, capped at 45 seconds
- FR17: The server returns early when job completion is detected before the timeout cap
- FR18: Claude Code can perform a single non-blocking status check (zero wait)

### Image Retrieval

- FR19: Claude Code can retrieve the absolute file path of a completed generation's output image
- FR20: The server organizes output images by date under a configurable output directory (`{output_dir}/{YYYY-MM-DD}/{filename}`)
- FR21: The server sanitizes image filenames via `os.path.basename()` before any file operations

### Error Handling

- FR22: The server returns structured error responses with fields: status, error message, error type, and retry suggestion
- FR23: The server classifies errors as transient (unreachable, generation_failed, storage_error) with `retry_suggested: true`
- FR24: The server classifies errors as terminal (invalid_inputs, invalid_workflow, model_not_found, directory_not_found, permission_denied, completed_no_output) with `retry_suggested: false`
- FR25: Error messages include enough context for Claude Code to explain the problem and suggest corrective action without user debugging

### Configuration

- FR26: The server reads ComfyUI base URL from `COMFYUI_URL` environment variable, defaulting to `http://localhost:8188`
- FR27: The server reads template directory from `COMFYCLAUDE_TEMPLATES_DIR` environment variable, defaulting to `./templates`
- FR28: The server reads output directory from `COMFYCLAUDE_OUTPUT_DIR` environment variable, defaulting to `./output`

### Out-of-Box Experience

- FR29: The server ships with two starter templates (`flux2_klein`, `flux2_klein_ultrawide`) that work immediately with a standard ComfyUI installation
- FR30: The server operates over stdio transport for direct integration with Claude Code's MCP configuration

## Non-Functional Requirements

### Performance

- HTTP request timeout: 30 seconds per httpx call to ComfyUI API
- Job polling overhead: negligible — 3-second sleep intervals with lightweight GET requests
- Template discovery: filesystem scan on each call (no caching required for single-user use)
- Image file operations: standard synchronous I/O (no streaming or chunked transfers needed)

### Integration

- ComfyUI API dependency: single external system, HTTP-only, no authentication
- Startup behavior: fail fast if ComfyUI is unreachable at server initialization — do not start the MCP server if the ComfyUI instance cannot be reached
- Connection resilience: transient failures during operation return structured errors with `retry_suggested: true`; server does not crash on ComfyUI unavailability after startup
- API compatibility: no version negotiation — server targets current ComfyUI HTTP API (`/prompt`, `/history/{id}`, `/view`); document known-working ComfyUI version
