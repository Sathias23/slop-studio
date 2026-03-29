---
title: "Product Brief: ComfyClaude"
status: "complete"
created: "2026-03-29"
updated: "2026-03-29"
inputs:
  - cenobite-agent/tools/image_generation.py
  - cenobite-agent/workflows/*.meta.json
  - web research (ComfyUI MCP landscape)
  - user conversation (Brad)
---

# Product Brief: ComfyClaude

## Executive Summary

ComfyClaude is an MCP server that gives Claude Code native tools to interact with a local ComfyUI instance. It lets you say "make a picture" and have Claude Code select a workflow template, craft the prompt, queue the job, wait for completion, and hand back the file path — all without leaving your terminal.

The local AI image generation space has exploded, but the tooling to orchestrate it programmatically is fragmented. Over 15 ComfyUI MCP servers exist today, yet none delivers a focused, local-first experience with proper template management and completion polling. ComfyClaude fills that gap by extracting battle-tested patterns from an existing production agent (cenobite-agent) and wrapping them in a clean MCP interface purpose-built for personal creative workflows on macOS.

**The pitch in one line:** Five tools, not forty. Clone, configure, generate.

## The Problem

ComfyUI is powerful but manual. Every generation requires opening a browser, wiring up nodes, tweaking parameters, and waiting. If you want Claude Code to help with creative work — generating concept art, iterating on visual ideas, producing project assets like favicons and OG images — it currently has no way to talk to your local ComfyUI instance.

Existing MCP solutions are either kitchen-sink servers with 40+ tools targeting cloud deployment, or minimal wrappers that lack workflow templating and completion polling. The kitchen-sink approach buries simple use cases under complexity. The minimal approach forces you to manage workflow JSON by hand and manually check if jobs finished. Neither respects the "say what you want, get what you need" workflow that makes Claude Code effective.

**Competitive landscape:**

| Project | Tools | Template Mgmt | Completion Polling | Transport | Gap |
|---------|-------|---------------|-------------------|-----------|-----|
| joenorton/comfyui-mcp-server (244 stars) | ~10 | No | No | HTTP | Ephemeral state, no polling |
| artokun/comfyui-mcp (21 stars) | 31 | Partial | Yes | stdio | Node.js, heavy/complex |
| Peleke/comfyui-mcp | 40 | No | Yes | HTTP | Cloud-first (Fly.io/Supabase) |
| IO-AtelierTech/comfyui-mcp (9 stars) | 40+ | No | No | HTTP | Immature (4 commits) |

## The Solution

A Python MCP server (built on FastMCP) that exposes five focused tool categories over stdio transport:

- **Template Discovery** — List available workflow templates with metadata (model, expected duration, supported aspect ratios, required inputs). Rich tool descriptions guide Claude Code to the right template for the user's intent.
- **Template Management** — Add, update, and delete workflow templates. The server manages its own template directory — `.json` workflow files paired with `.meta.json` metadata sidecars. Schema validation on write prevents malformed templates from entering the registry.
- **Job Submission** — Queue a generation job from a template name, input parameters, and optional aspect ratio. The server handles input injection, seed randomization, and resolution mapping.
- **Job Monitoring** — Poll a running job for completion status with configurable wait duration (default: poll every 3s, cap at 45s), returning early when done. Returns structured status: pending, running, completed, or failed with error details.
- **Image Retrieval** — Get the file path of a completed generation, organized by date under a configurable output directory.

Claude Code becomes the orchestration layer: it reads available templates, selects the right one for the user's intent, crafts technical prompts from natural language, submits jobs, and delivers results — all within the conversation flow.

**Error handling:** Structured error responses distinguish transient failures (ComfyUI unreachable, timeout — retry suggested) from terminal ones (invalid template, missing input — fix required). Claude Code can self-correct based on the error type.

## What Makes This Different

**Focused, not sprawling.** Five tool categories, not forty. Designed for one person generating images locally, not a team deploying to the cloud. Zero API costs, full privacy — your images never leave your machine.

**Template-driven.** Workflow templates are first-class citizens with structured metadata sidecars. The `.json` + `.meta.json` pair design means templates are human-readable, git-trackable, and shareable without any database or registry. Claude Code knows what each template does, what inputs it needs, and what aspect ratios it supports — before submitting a single job.

**Completion polling built in.** No fire-and-forget. The server polls ComfyUI's history API and returns when the job is done or the timeout is reached. Claude Code can wait or check back later.

**Proven patterns.** Core logic extracted from cenobite-agent's `image_generation.py` — a production system with robust error handling across known failure modes (VRAM OOM, model not found, prompt rejection, connection failures), seed randomization to bust ComfyUI's cache, and path sanitization to prevent traversal attacks.

**File paths, not ephemeral IDs.** Generated images are referenced by absolute file path, organized by date. Nothing is lost on restart.

## Who This Serves

**Primary user:** Brad — a developer running ComfyUI locally on macOS with Apple Silicon, who wants Claude Code to be a creative partner that can generate and iterate on images as part of a conversational workflow. Use cases range from creative exploration to generating project assets (icons, placeholders, concept art) inline during development.

**Future potential:** Any developer or creative running a local ComfyUI instance who wants to integrate image generation into their Claude Code workflow.

## Success Criteria

- Claude Code can list templates, queue a job, and return an image path in a single conversational exchange
- Template CRUD works reliably — add a new workflow, use it immediately
- Job polling returns correct status and handles timeouts gracefully
- Error messages are actionable enough for Claude Code to self-correct without user intervention
- **Setup under 5 minutes:** clone, set `COMFYUI_URL`, add MCP config snippet, generate first image
- Ships with a starter template (flux2_klein from cenobite-agent) so the out-of-box experience works immediately

## Scope

**v1 (MVP):**
- `list_templates` / `get_template` — browse and inspect workflow templates
- `add_template` / `update_template` / `delete_template` — full template CRUD with schema validation
- `queue_prompt` — submit a job from template + inputs + optional aspect ratio
- `check_job` — poll for completion, return status and image path
- `get_image` — retrieve image file path by job or filename
- Configuration: `COMFYUI_URL` (default: `http://localhost:8188`), `COMFYCLAUDE_TEMPLATES_DIR`, `COMFYCLAUDE_OUTPUT_DIR`
- Python + FastMCP, stdio transport
- Workflow template format: `.json` + `.meta.json` pairs (same schema as cenobite-agent)
- Starter template: `flux2_klein` (migrated from cenobite-agent) for out-of-box experience
- Unit tests with mocked ComfyUI API

**Explicitly out of scope for v1:**
- Model management (`list_models`, `load_model`)
- Job cancellation
- Raw/arbitrary workflow submission
- Cloud or multi-user deployment
- Video, audio, or non-image generation
- WebSocket-based progress streaming
- Web UI or dashboard

## Risks

- **ComfyUI API instability** — No stable versioned API; endpoint shapes can change between ComfyUI updates. Mitigation: pin to known-working ComfyUI version, document compatibility.
- **Template brittleness** — Workflow JSON is coupled to installed custom nodes and model filenames. A template breaks if the user updates nodes or renames models. Mitigation: clear error messages when a template references missing nodes/models.
- **Scope creep** — The out-of-scope list is long and tempting. Mitigation: the "five tools, not forty" principle is the design constraint, not a temporary limitation.

## Vision

If ComfyClaude works well for personal use, it becomes the foundation for richer creative workflows: iterative generation loops (generate variants, pick one, refine), batch generation, automated asset pipelines for projects. The template system — already git-trackable and shareable — could grow into a community library. The MCP interface could expand to cover model management and real-time progress. But the core principle stays: Claude Code as creative collaborator, ComfyUI as the engine, templates as the bridge.
