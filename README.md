# Slop Studio

MCP server for conversational image generation via ComfyUI.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- ComfyUI running and accessible over HTTP

**ComfyUI version:** Latest stable release recommended (not nightly builds)

## Setup

1. Clone the repository:

   ```bash
   git clone https://github.com/Sathias23/slop-studio.git
   cd slop-studio
   ```

2. Install dependencies:

   ```bash
   uv sync
   ```

3. Set the ComfyUI URL (defaults to `http://localhost:8188`):

   ```bash
   export COMFYUI_URL="http://localhost:8188"
   ```

4. Start ComfyUI, then verify the server starts:

   ```bash
   uv run main.py
   ```

## MCP Configuration

Add the following to your Claude Code MCP config (`~/.claude/mcp_servers.json` or project-level):

```json
{
  "mcpServers": {
    "slop-studio": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/slop-studio", "main.py"]
    }
  }
}
```

Replace `/path/to/slop-studio` with the absolute path to the cloned repository.

## Development

Run tests:

```bash
uv run pytest
```
