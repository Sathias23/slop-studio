import json
import logging
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).parent / "assets"


def init_project(target: Path) -> bool:
    """Scaffold an art project directory with starter templates, MCP config, and slash commands."""
    target = target.resolve()
    repo_path = Path(__file__).parent.parent.resolve()

    # 1. Copy starter templates
    templates_dir = target / "templates"
    templates_dir.mkdir(exist_ok=True)
    for f in (ASSETS_DIR / "starter-templates").iterdir():
        shutil.copy2(f, templates_dir / f.name)

    # 2. Generate .mcp.json (skip if exists)
    mcp_json_path = target / ".mcp.json"
    if mcp_json_path.exists():
        print(f"Warning: {mcp_json_path} already exists — skipping", file=sys.stderr)
    else:
        mcp_config = {
            "mcpServers": {
                "slop-studio": {
                    "command": "uv",
                    "args": ["run", "--directory", str(repo_path), "main.py",
                             "--project-dir", str(target)],
                    "env": {}
                }
            }
        }
        mcp_json_path.write_text(json.dumps(mcp_config, indent=2))

    # 3. Copy slash commands
    commands_dir = target / ".claude" / "commands"
    commands_dir.mkdir(parents=True, exist_ok=True)
    for f in (ASSETS_DIR / "claude-commands").iterdir():
        shutil.copy2(f, commands_dir / f.name)

    # 4. Copy CLAUDE.md template (skip if exists)
    claude_md_path = target / "CLAUDE.md"
    if claude_md_path.exists():
        print(f"Warning: {claude_md_path} already exists — skipping", file=sys.stderr)
    else:
        shutil.copy2(ASSETS_DIR / "claude-md-template.md", claude_md_path)

    logger.info("Art project scaffolded at %s", target)
    print(f"✓ Art project initialized at {target}")
    print(f"  templates/       — {len(list(templates_dir.iterdir()))} starter templates")
    print(f"  .mcp.json        — MCP server config for Claude Code")
    print(f"  .claude/commands — /generate slash command")
    print(f"  CLAUDE.md        — project instructions for Claude Code")
    print(f"\nOpen Claude Code in this directory to start generating images.")
    return True
