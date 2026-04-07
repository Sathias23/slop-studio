import json
import logging
import shlex
import shutil
import sys
import tomllib
from pathlib import Path

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).parent / "assets"
_CONFIG_DIR = Path.home() / ".config" / "slop-studio"
_CONFIG_FILE = _CONFIG_DIR / "config.toml"

# Common locations where ComfyUI may be installed
_COMFYUI_SEARCH_PATHS = [
    Path.home() / "ComfyUI",
    Path.home() / "comfyui",
    Path("/opt/ComfyUI"),
    Path("/opt/comfyui"),
]


def _find_python_for(comfyui_dir: Path) -> str:
    """Find the best Python executable to run ComfyUI in its own environment."""
    # Prefer a venv inside the ComfyUI directory
    venv_python = comfyui_dir / "venv" / "bin" / "python"
    if venv_python.is_file():
        return shlex.quote(str(venv_python))
    venv_python_win = comfyui_dir / "venv" / "Scripts" / "python.exe"
    if venv_python_win.is_file():
        return shlex.quote(str(venv_python_win))
    # Fall back to python3 on PATH (more reliable than bare 'python')
    python3 = shutil.which("python3")
    if python3:
        return shlex.quote(python3)
    python = shutil.which("python")
    if python:
        return shlex.quote(python)
    return "python3"


def _load_config_toml() -> dict:
    """Read config.toml, returning {} on any failure."""
    try:
        with open(_CONFIG_FILE, "rb") as f:
            return tomllib.load(f)
    except (FileNotFoundError, tomllib.TOMLDecodeError, OSError):
        return {}


def _save_to_config_toml(key: str, value: str) -> None:
    """Read existing config.toml, set *key* = *value*, and write back.

    Only string values are preserved; non-string values (tables, arrays)
    are dropped to avoid producing invalid TOML.
    """
    config = _load_config_toml()
    config[key] = value
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        f'{k} = {json.dumps(v)}'
        for k, v in sorted(config.items())
        if isinstance(v, str)
    ]
    _CONFIG_FILE.write_text("\n".join(lines) + "\n")


def _read_saved_comfyui_cmd() -> str:
    """Return saved comfyui_start_cmd from config.toml, or empty string."""
    return _load_config_toml().get("comfyui_start_cmd", "")


def _prompt_comfyui_cmd(saved_default: str) -> str:
    """Interactively ask the user for their ComfyUI start command.

    Shows *saved_default* in brackets if available. Returns the command string,
    or empty string if the user provides no input and there is no default.
    """
    if saved_default:
        prompt = f"  ComfyUI start command [{saved_default}]: "
    else:
        prompt = "  ComfyUI start command (e.g. python3 ~/ComfyUI/main.py): "

    answer = input(prompt).strip()
    return answer if answer else saved_default


def _detect_comfyui_start_cmd() -> str:
    """Try to find a ComfyUI installation and return a start command, or empty string."""
    # Check if 'comfyui' is on PATH
    if shutil.which("comfyui"):
        return "comfyui"

    # Check common install directories for main.py
    for candidate in _COMFYUI_SEARCH_PATHS:
        main_py = candidate / "main.py"
        if main_py.is_file():
            python = _find_python_for(candidate)
            return f"{python} {shlex.quote(str(main_py))}"

    return ""


def init_project(target: Path) -> bool:
    """Scaffold an art project directory with starter templates, MCP config, and slash commands."""
    target = target.resolve()

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
        detected = _detect_comfyui_start_cmd()
        saved = _read_saved_comfyui_cmd()
        # Best available default: saved config > auto-detected > placeholder
        default_cmd = saved or detected or ""
        env = {}

        if sys.stdin.isatty():
            if detected and not saved:
                print(f"  Detected ComfyUI: {detected}")
            elif not detected and not saved:
                print("  ComfyUI not found on PATH or in common locations.")
            start_cmd = _prompt_comfyui_cmd(default_cmd)
            if start_cmd:
                if start_cmd != saved:
                    _save_to_config_toml("comfyui_start_cmd", start_cmd)
                    print(f"  Saved to {_CONFIG_FILE}")
                env["COMFYUI_START_CMD"] = start_cmd
            else:
                env["COMFYUI_START_CMD"] = "python3 ~/ComfyUI/main.py"
                print(
                    "  No command provided.\n"
                    "  Edit .mcp.json to set COMFYUI_START_CMD to your ComfyUI start command."
                )
        elif saved:
            env["COMFYUI_START_CMD"] = saved
            print(f"  Using saved ComfyUI command: {saved}")
        elif detected:
            env["COMFYUI_START_CMD"] = detected
            print(f"  Detected ComfyUI: {detected}")
        else:
            env["COMFYUI_START_CMD"] = "python3 ~/ComfyUI/main.py"
            print(
                "  ComfyUI not found on PATH or in common locations.\n"
                "  Edit .mcp.json to set COMFYUI_START_CMD to your ComfyUI start command."
            )

        mcp_config = {
            "mcpServers": {
                "slop-studio": {
                    "command": "slop-studio",
                    "args": ["serve", "--project-dir", str(target)],
                    "env": env
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
