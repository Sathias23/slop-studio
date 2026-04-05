# Slop Studio

MCP server for conversational image generation via ComfyUI. Generate images through natural conversation in Claude Code — describe what you want, and slop-studio handles template selection, job submission, polling, and output.

*slop-studio powers [@generatedhorror.bsky.social](https://bsky.app/profile/generatedhorror.bsky.social), my horror-themed AI art account.*

![Slop Studio](docs/image.png)

## Features

- Conversational image generation through Claude Code
- Support for text-to-image and image-to-image models, currently Flux.2 Klein (more to come)
- Workflow template system with browsing, customization, and aspect ratios
- Job queuing and automatic polling
- Bluesky posting built in
- One-command project scaffolding (`slop-studio init`)

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- ComfyUI running and accessible over HTTP (default: `http://localhost:8188`)

## Install

```bash
uv tool install git+https://github.com/Sathias23/slop-studio.git
```

This puts the `slop-studio` command on your PATH.

### Install from source

```bash
git clone https://github.com/Sathias23/slop-studio.git
cd slop-studio
uv tool install -e .
```

This installs `slop-studio` on your PATH from your local clone. Changes you make to the source take effect immediately.

## Quick Start

1. **Start ComfyUI** on your local machine or network.

2. **Set up a project directory:**

   ```bash
   mkdir my-art && cd my-art
   slop-studio init
   ```

   This scaffolds the directory with starter templates, `.mcp.json` (MCP server config for Claude Code), a `/generate` slash command, and a `CLAUDE.md`.

3. **Open Claude Code** in the project directory and use `/generate` to create images:

   ```
   /generate a sunset over mountains, cinematic lighting
   ```

## Bluesky Posting

To post generated images to Bluesky, configure your credentials once:

```bash
slop-studio auth
```

This stores your handle and app password in `~/.config/slop-studio/credentials.json` (mode 0600). The MCP server picks them up automatically — no per-project configuration needed.

Create an app password at [bsky.app](https://bsky.app) > Settings > App Passwords.

## CLI

```
slop-studio auth     Configure Bluesky credentials
slop-studio init     Scaffold an art project directory
slop-studio serve    Launch the MCP server (used by .mcp.json)
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `list_templates` | Browse available workflow templates |
| `get_template` | Inspect inputs and aspect ratios for a template |
| `queue_prompt` | Submit a generation job |
| `check_next_job` | Poll multiple jobs for completion |
| `get_image` | Retrieve the output image path |
| `post_to_bluesky` | Post image(s) to Bluesky with text and hashtags |
| `add_template` | Register a new ComfyUI workflow |
| `update_template` | Update an existing template |
| `delete_template` | Remove a template |

## Templates

Workflow templates live in `templates/` as `.json` + `.meta.json` pairs. Three starter templates ship with every project:

- **flux2_klein** — fast single-pass generation (~30s), 9 aspect ratios
- **flux2_klein_ultrawide** — 3440x1440 wallpapers with 4x upscale (~60s)
- **flux2_klein_edit** — multi-reference image editing with style/content transfer (~60s)

The default templates run on 16GB of VRAM. Add your own by exporting a workflow from ComfyUI's browser UI and calling `add_template`.

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `COMFYUI_URL` | `http://localhost:8188` | ComfyUI server address |
| `SLOP_STUDIO_TEMPLATES_DIR` | `./templates` | Template directory |
| `SLOP_STUDIO_OUTPUT_DIR` | `./output` | Output directory |
| `BSKY_HANDLE` | — | Bluesky handle (overrides central config) |
| `BSKY_APP_PASSWORD` | — | Bluesky app password (overrides central config) |

Environment variables take precedence over `slop-studio auth` credentials. A project-level `.env` file is also supported.

## Coming Soon

- More workflow templates (SDXL, video, inpainting, LoRA stacks)
- ComfyUI launcher and process management
- Model downloading and management tools
- The Sloppifier — token and prompt manipulation tools
- Claude Code personas and lore system

## Troubleshooting

**MCP server not connecting:** If you start Claude Code before ComfyUI is running, the MCP server won't be able to connect. Use the `/mcp` command inside Claude Code to reconnect after starting ComfyUI.

## Development

```bash
git clone https://github.com/Sathias23/slop-studio.git
cd slop-studio
uv sync
uv run python -m pytest
```
