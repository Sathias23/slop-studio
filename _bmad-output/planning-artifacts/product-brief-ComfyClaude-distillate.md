---
title: "Product Brief Distillate: ComfyClaude"
type: llm-distillate
source: "product-brief-ComfyClaude.md"
created: "2026-03-29"
purpose: "Token-efficient context for downstream PRD creation"
---

# ComfyClaude — Detail Pack

## Template Schema (from cenobite-agent)

- Templates are `.json` + `.meta.json` pairs in a single directory, discovered at call-time via filesystem scan (no registry/database)
- **Meta.json required fields:** `name`, `description`, `model`, `anticipated_duration`, `inputs[]`
- **Meta.json optional fields:** `visual_approach`, `wings[]`, `themes[]`, `aspect_ratios{}`, `resolution_nodes[]`
- **Input definition fields:** `name` (key), `node_id` (target node), `field` (target field name), `required` (bool), `description`, `default`
- **Aspect ratio structure:** `{"1:1": [1424, 1424], "16:9": [1888, 1064], ...}` — maps label to `[width, height]` tuple
- **Resolution nodes:** `[{"node_id": "47", "width_field": "width", "height_field": "height"}]` — where to inject dimensions
- Templates without `aspect_ratios` (empty `{}`) accept only their fixed resolution — no aspect ratio param
- Starter template: `flux2_klein` — Flux 2 Klein 9B quantized, ~30s generation, supports multiple aspect ratios (1:1, 4:3, 3:4, 3:2, 2:3, 16:9, 9:16, 21:9, 43:18)

## ComfyUI API Surface

- **Submit:** `POST /prompt` with `{"prompt": workflow_dict}` → returns `{"prompt_id": "uuid"}`
- **Poll:** `GET /history/{prompt_id}` → `{prompt_id: {status: {status_str, completed, messages}, outputs: {node_id: {images: [{filename, subfolder, type}]}}}}`
- **Fetch image:** `GET /view?filename=X&subfolder=Y&type=Z` → binary image data
- **No stable versioned API** — endpoint shapes can change between ComfyUI commits
- Default URL: `http://localhost:8188` (native macOS install)
- HTTP timeout: 30s per request (httpx)

## Polling Strategy

- Default poll interval: 3 seconds
- Default poll cap: 45 seconds
- `wait_before=0` → single check, no wait (useful for non-blocking check-back pattern)
- Job not in history = pending; `completed=false` = running; `completed=true` + images = completed; `completed=true` + no images = completed_no_output
- Returns early on completion, continues polling if elapsed < cap

## Input Injection Mechanism

- For each input def: `workflow[node_id]["inputs"][field] = user_value`
- Seed fields (`seed`, `noise_seed`) randomized via `random.randint(0, 2**53 - 1)` across all nodes to bust ComfyUI cache
- Aspect ratio injection: look up `[width, height]` from template's `aspect_ratios` map, inject into each `resolution_nodes` entry
- Path sanitization: `template_name` cannot contain `/`, `..`, or start with `.`; image filenames sanitized via `os.path.basename()`

## Error Type Taxonomy

- **Transient (retry_suggested: true):** `unreachable` (ConnectError/TimeoutException), `generation_failed` (5xx), `storage_error` (filesystem write failure)
- **Terminal (retry_suggested: false):** `invalid_inputs` (missing required input), `invalid_workflow` (bad template), `model_not_found` (4xx from ComfyUI), `directory_not_found`, `permission_denied`, `completed_no_output`
- All errors structured as: `{status: "error", error: str, error_type: str, retry_suggested: bool}`
- MCP server should use native structured responses, not JSON strings (cenobite-agent used JSON strings due to Letta sandbox constraint)

## Configuration

- `COMFYUI_URL` — ComfyUI base URL, default `http://localhost:8188`
- `COMFYCLAUDE_TEMPLATES_DIR` — where templates live, default: `./templates` relative to server
- `COMFYCLAUDE_OUTPUT_DIR` — where images are saved, default: `./output`
- Image output organized as `{output_dir}/{YYYY-MM-DD}/{filename}`
- Recognized image extensions: `.png`, `.jpg`, `.jpeg`, `.webp`

## Competitive Intelligence

- **15+ ComfyUI MCP servers exist** as of March 2026; none has emerged as the clear winner
- **joenorton/comfyui-mcp-server** (244 stars) is most popular but has ephemeral state (asset IDs lost on restart), no completion polling, limited templating
- **artokun/comfyui-mcp** (21 stars) is most feature-complete (31 tools, SQLite, slash commands) but requires Node.js 22+ and is heavy/complex
- **Peleke/comfyui-mcp** has 40 tools but targets cloud deployment (Fly.io + RunPod + Supabase), not local use
- ComfyUI team itself is engaging with MCP developers — robinjhuang opened issues asking what upstream API changes would help
- FastMCP (Python) is the de facto framework; reduces MCP boilerplate to ~30 lines
- Market splitting between kitchen-sink (40+ tools, cloud) and minimal (5 tools, no templates) — gap exists for focused local-first

## Rejected / Parked Ideas (do not re-propose for v1)

- **Raw/arbitrary workflow submission** — parked; templates are the abstraction layer, raw JSON bypasses the value prop
- **Model management (list_models, load_model)** — parked for v2+; out of scope for template-driven MVP
- **Job cancellation** — parked; low priority for personal use where jobs are short
- **WebSocket progress streaming** — parked; polling is simpler and sufficient for v1
- **Cloud/multi-user deployment** — explicitly out of scope; local-first is the positioning
- **Video/audio generation** — parked; image-only for v1
- **MCP resources for template discovery** — considered as alternative to list_templates tool; may be premature optimization
- **SSE/HTTP transport** — stdio is fine for personal Claude Code use; revisit if shared access needed
- **Persistent httpx.AsyncClient via lifespan** — considered for performance; may be over-engineering for typical latency
- **Seed control (fixed seeds for reproducibility)** — open idea, not prioritized for v1; default is randomize
- **Image filtering by template name** — open idea for finding related generations; not in v1

## Requirements Hints (captured from conversation)

- Templates must be self-managed by the MCP server — user should not need to manually edit JSON
- Schema validation on template write to prevent malformed entries
- Tool descriptions must be rich enough for Claude Code to select the right template without user guidance
- Empty state must not be a dead end — starter template required
- File paths returned as absolute paths (shared filesystem between ComfyUI and Claude Code)
- The "make a picture" flow should work in a single conversational exchange (list → select → queue → poll → return path)

## Technical Context

- **Platform:** macOS, Apple Silicon, ComfyUI running natively (not in Docker)
- **Python:** 3.11+, managed by `uv`
- **Key dependency from cenobite-agent:** `httpx>=0.28.1` for HTTP client
- **MCP framework:** FastMCP (Python), stdio transport
- **Source code to extract from:** `/Users/sathias/Agents/Project-Cenobite/cenobite-agent/tools/image_generation.py` (core logic), `/Users/sathias/Agents/Project-Cenobite/cenobite-agent/workflows/` (template examples)
- Cenobite-agent tools use Letta sandbox conventions (imports inside functions, JSON string returns) — these constraints do NOT apply to the MCP server

## Open Questions

- What schema validation rules for template CRUD? Full ComfyUI workflow validation or just meta.json structure?
- Should `check_job` return the image file path directly, or should `get_image` be a separate step?
- How to handle ComfyUI being down at MCP server startup — fail fast or lazy connection?
- Should template `add` accept a ComfyUI export directly, or require the user to provide both `.json` and `.meta.json`?
