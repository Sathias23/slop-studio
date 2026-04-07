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


def _python_from_venv(venv_dir: Path) -> str | None:
    """Return the python executable inside a venv, or None if not found."""
    for candidate in [
        venv_dir / "bin" / "python",
        venv_dir / "Scripts" / "python.exe",
    ]:
        if candidate.is_file():
            return str(candidate)
    return None


def _detect_comfyui_dir() -> Path | None:
    """Check common ComfyUI locations, return the directory or None."""
    for candidate in _COMFYUI_SEARCH_PATHS:
        if (candidate / "main.py").is_file():
            return candidate
    return None


def _build_start_cmd(comfyui_dir: Path, venv_dir: Path | None = None) -> str:
    """Construct a start command from a ComfyUI dir and optional venv."""
    if venv_dir:
        python_path = _python_from_venv(venv_dir)
        if python_path:
            return f"{shlex.quote(python_path)} {shlex.quote(str(comfyui_dir / 'main.py'))}"
    python = _find_python_for(comfyui_dir)
    return f"{python} {shlex.quote(str(comfyui_dir / 'main.py'))}"


def _prompt_path(label: str, default: str) -> str:
    """Prompt for a filesystem path, showing default in brackets."""
    if default:
        answer = input(f"  {label} [{default}]: ").strip()
    else:
        answer = input(f"  {label}: ").strip()
    return answer if answer else default


def _prompt_comfyui_setup(saved_config: dict, detected_dir: Path | None) -> str | None:
    """Interactively ask the user for ComfyUI dir and venv. Returns start command or None."""
    saved_dir = saved_config.get("comfyui_dir", "")
    saved_venv = saved_config.get("comfyui_venv", "")

    # Default to detected dir, then saved dir
    default_dir = str(detected_dir) if detected_dir else saved_dir

    dir_str = _prompt_path("ComfyUI directory", default_dir)
    if not dir_str:
        return None

    comfyui_dir = Path(dir_str).expanduser().resolve()
    if not (comfyui_dir / "main.py").is_file():
        print(f"  Warning: {comfyui_dir / 'main.py'} not found")

    # Check for venv inside ComfyUI dir first
    builtin_venv = comfyui_dir / "venv"
    if _python_from_venv(builtin_venv):
        default_venv = str(builtin_venv)
    else:
        default_venv = saved_venv

    venv_str = _prompt_path("Python venv directory", default_venv)
    venv_dir = Path(venv_str).expanduser().resolve() if venv_str else None

    if venv_dir and not _python_from_venv(venv_dir):
        print(f"  Warning: no python found in {venv_dir}/bin/ or {venv_dir}/Scripts/")

    cmd = _build_start_cmd(comfyui_dir, venv_dir)

    # Save paths and constructed command for next time
    _save_to_config_toml("comfyui_dir", str(comfyui_dir))
    if venv_dir:
        _save_to_config_toml("comfyui_venv", str(venv_dir))
    _save_to_config_toml("comfyui_start_cmd", cmd)
    print(f"  Saved to {_CONFIG_FILE}")

    return cmd


def _detect_comfyui_start_cmd() -> str:
    """Try to find a ComfyUI installation and return a start command, or empty string."""
    if shutil.which("comfyui"):
        return "comfyui"

    detected = _detect_comfyui_dir()
    if detected:
        return _build_start_cmd(detected)

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
        env = {}
        saved_config = _load_config_toml()
        detected_dir = _detect_comfyui_dir()

        if sys.stdin.isatty():
            if detected_dir:
                print(f"  Found ComfyUI at {detected_dir}")
            else:
                print("  ComfyUI not found on PATH or in common locations.")
            start_cmd = _prompt_comfyui_setup(saved_config, detected_dir)
            if start_cmd:
                env["COMFYUI_START_CMD"] = start_cmd
            else:
                env["COMFYUI_START_CMD"] = "python3 ~/ComfyUI/main.py"
                print(
                    "  No directory provided.\n"
                    "  Edit .mcp.json to set COMFYUI_START_CMD to your ComfyUI start command."
                )
        else:
            # Non-interactive: use saved command, or auto-detect, or placeholder
            saved_cmd = saved_config.get("comfyui_start_cmd", "")
            if saved_cmd:
                env["COMFYUI_START_CMD"] = saved_cmd
                print(f"  Using saved ComfyUI command: {saved_cmd}")
            elif detected_dir:
                start_cmd = _build_start_cmd(detected_dir)
                env["COMFYUI_START_CMD"] = start_cmd
                print(f"  Detected ComfyUI: {start_cmd}")
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
