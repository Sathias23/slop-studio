"""CLI entry point for slop-studio."""

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "slop-studio"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"


def _auth(args: argparse.Namespace) -> None:
    """Prompt for Bluesky credentials and save to central config."""
    if CREDENTIALS_FILE.exists():
        answer = input("Existing credentials found. Overwrite? [Y/n] ").strip().lower()
        if answer not in ("", "y", "yes"):
            print("Aborted.")
            return

    handle = input("Bluesky handle (e.g. you.bsky.social): ").strip()
    if not handle:
        print("Error: handle cannot be empty.", file=sys.stderr)
        sys.exit(1)

    app_password = getpass.getpass("App password: ").strip()
    if not app_password:
        print("Error: app password cannot be empty.", file=sys.stderr)
        sys.exit(1)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    credentials = {"bluesky": {"handle": handle, "app_password": app_password}}
    fd = os.open(str(CREDENTIALS_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(credentials, f, indent=2)

    print(f"✓ Bluesky credentials saved to {CREDENTIALS_FILE}")
    print("  Your MCP server will pick these up automatically.")


def _init(args: argparse.Namespace) -> None:
    """Scaffold an art project directory."""
    from slop_studio.init import init_project

    target = Path(args.path).resolve() if args.path else Path.cwd()
    success = init_project(target)
    sys.exit(0 if success else 1)


def _detect_slop_studio_path() -> tuple[str, list[str]]:
    """Find the slop-studio binary; returns (command, extra_args_prefix).

    When slop-studio is on PATH: ("path/to/slop-studio", [])
    Fallback:                    ("uv", ["tool", "run", "slop-studio"])
    """
    path = shutil.which("slop-studio")
    if path:
        return path, []
    return "uv", ["tool", "run", "slop-studio"]


def _detect_comfyui() -> str:
    """Check common ComfyUI locations, return start command or placeholder."""
    import shlex

    common_paths = [
        Path.home() / "ComfyUI" / "main.py",
        Path.home() / "comfyui" / "main.py",
        Path("/opt/ComfyUI/main.py"),
    ]
    for p in common_paths:
        if p.exists():
            return f"{shlex.quote(sys.executable)} {shlex.quote(str(p))} --port 8188"
    return f"{shlex.quote(sys.executable)} /path/to/ComfyUI/main.py --port 8188"


def _copy_to_clipboard(text: str) -> None:
    """Copy text to system clipboard if available."""
    commands = [
        ["pbcopy"],
        ["xclip", "-selection", "clipboard"],
        ["clip"],
    ]
    for cmd in commands:
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text.encode(), check=True, timeout=5)
                print("Copied to clipboard.", file=sys.stderr)
                return
            except (subprocess.SubprocessError, subprocess.TimeoutExpired):
                pass
    print("Could not copy to clipboard — paste the JSON above manually.", file=sys.stderr)


def _desktop_config(args: argparse.Namespace) -> None:
    """Print claude_desktop_config.json snippet for Claude Desktop setup."""
    command, extra_args = _detect_slop_studio_path()
    comfyui_cmd = _detect_comfyui()

    config = {
        "mcpServers": {
            "slop-studio": {
                "command": command,
                "args": extra_args + ["serve"],
                "env": {
                    "COMFYUI_URL": "http://localhost:8188",
                    "COMFYUI_START_CMD": comfyui_cmd,
                    "SLOP_STUDIO_OUTPUT_DIR": str(Path.home() / "slop-studio" / "output"),
                },
            }
        }
    }

    snippet = json.dumps(config, indent=2)
    print(snippet)
    print("\nPaste this into your claude_desktop_config.json:", file=sys.stderr)
    print("  macOS: ~/Library/Application Support/Claude/claude_desktop_config.json", file=sys.stderr)
    print("  Windows: %APPDATA%\\Claude\\claude_desktop_config.json", file=sys.stderr)
    print("  Linux: ~/.config/Claude/claude_desktop_config.json", file=sys.stderr)
    print("\nThen restart Claude Desktop.", file=sys.stderr)

    if args.copy:
        _copy_to_clipboard(snippet)


def _build_mcpb(args: argparse.Namespace) -> None:
    """Build a .mcpb Desktop Extension package."""
    from slop_studio.mcpb import build_mcpb

    project_root = Path(__file__).resolve().parent.parent
    output_dir = Path(args.output_dir).resolve() if args.output_dir else Path.cwd()
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        output_path = build_mcpb(project_root, output_dir)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    print(output_path)


def _serve(args: argparse.Namespace) -> None:
    """Launch the MCP server."""
    # Handle --project-dir env setup before importing server
    if args.project_dir:
        project_dir = str(Path(args.project_dir).resolve())
        os.environ.setdefault("SLOP_STUDIO_OUTPUT_DIR", os.path.join(project_dir, "output"))
        os.environ.setdefault("SLOP_STUDIO_TEMPLATES_DIR", os.path.join(project_dir, "templates"))
        from dotenv import load_dotenv
        load_dotenv(os.path.join(project_dir, ".env"))

    from slop_studio.server import mcp
    mcp.run(transport="stdio")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="slop-studio",
        description="Conversational image generation via ComfyUI",
    )
    subparsers = parser.add_subparsers(dest="command")

    # auth
    subparsers.add_parser("auth", help="Configure Bluesky credentials")

    # init
    init_parser = subparsers.add_parser("init", help="Scaffold an art project directory")
    init_parser.add_argument("path", nargs="?", default=None, help="Target directory (default: current)")

    # serve
    serve_parser = subparsers.add_parser("serve", help="Launch the MCP server")
    serve_parser.add_argument("--project-dir", default=None, help="Art project directory for output/templates paths")

    # desktop-config
    dc_parser = subparsers.add_parser("desktop-config", help="Generate Claude Desktop config snippet")
    dc_parser.add_argument("--copy", action="store_true", help="Copy snippet to clipboard")

    # build-mcpb
    mcpb_parser = subparsers.add_parser("build-mcpb", help="Build .mcpb Desktop Extension package")
    mcpb_parser.add_argument("--output-dir", default=None, help="Output directory (default: current)")

    args = parser.parse_args()

    if args.command == "auth":
        _auth(args)
    elif args.command == "init":
        _init(args)
    elif args.command == "serve":
        _serve(args)
    elif args.command == "desktop-config":
        _desktop_config(args)
    elif args.command == "build-mcpb":
        _build_mcpb(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
