"""CLI entry point for slop-studio."""

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

CONFIG_DIR = Path.home() / ".config" / "slop-studio"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"


def _malformed_json_error(detail: str) -> NoReturn:
    print(f"Error: {CREDENTIALS_FILE} is not valid JSON ({detail}).", file=sys.stderr)
    print("  Fix it by hand or delete the file and re-run `slop-studio auth`.", file=sys.stderr)
    sys.exit(1)


def _load_existing_credentials() -> dict:
    """Read credentials.json into a dict, or return empty dict if absent.

    Malformed JSON (parse failure or a top-level value that isn't an object)
    is a terminal error — we don't auto-delete the file because it may contain
    values the user still wants to recover manually. Per-service blocks that
    aren't dicts are also rejected.
    """
    if not CREDENTIALS_FILE.exists():
        return {}
    try:
        data = json.loads(CREDENTIALS_FILE.read_text())
    except json.JSONDecodeError as e:
        _malformed_json_error(str(e))
    if not isinstance(data, dict):
        _malformed_json_error(f"top-level value must be an object, got {type(data).__name__}")
    for key in ("bluesky", "comfy_cloud"):
        if key in data and not isinstance(data[key], dict):
            _malformed_json_error(f"'{key}' must be an object, got {type(data[key]).__name__}")
    return data


def _write_credentials(credentials: dict) -> None:
    """Write the credentials dict atomically with mode 0600.

    Strategy: write to a sibling `.tmp` file at mode 0600, then `os.replace`
    onto the final path. `os.replace` is atomic on POSIX, so a crash between
    steps leaves either the old or the new file — never a truncated one. The
    final `chmod` reasserts 0600 even when replacing a pre-existing file that
    had permissive modes.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = CREDENTIALS_FILE.with_suffix(CREDENTIALS_FILE.suffix + ".tmp")
    fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(credentials, f, indent=2)
        tmp_path.replace(CREDENTIALS_FILE)
        CREDENTIALS_FILE.chmod(0o600)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def _confirm_overwrite(service_label: str) -> bool:
    """Ask the user whether to replace an existing non-empty credential block.

    Default is **No** — only "y" or "yes" proceeds. Stray Enter keeps the
    existing block, matching the industry convention for destructive prompts.
    """
    answer = input(f"Existing {service_label} credentials found. Overwrite? [y/N] ").strip().lower()
    return answer in ("y", "yes")


def _prompt_bluesky() -> dict:
    """Prompt for Bluesky credentials. Exits with code 1 on empty input."""
    handle = input("Bluesky handle (e.g. you.bsky.social): ").strip()
    if not handle:
        print("Error: handle cannot be empty.", file=sys.stderr)
        sys.exit(1)

    app_password = getpass.getpass("App password: ").strip()
    if not app_password:
        print("Error: app password cannot be empty.", file=sys.stderr)
        sys.exit(1)

    return {"handle": handle, "app_password": app_password}


def _prompt_comfy_cloud() -> dict:
    """Prompt for Comfy Cloud API key. Exits with code 1 on empty input."""
    print("  Used for Comfy Cloud submissions AND for any local workflow")
    print("  that includes a Comfy partner-API node (OpenAI GPT Image, Flux 2 Pro,")
    print("  Gemini/Nano Banana, etc.).")
    print("  Get a key at https://platform.comfy.org/profile/api-keys (shown once — copy it).")
    api_key = getpass.getpass("Comfy Cloud API key: ").strip()
    if not api_key:
        print("Error: API key cannot be empty.", file=sys.stderr)
        sys.exit(1)

    return {"api_key": api_key}


def _pick_services_interactive() -> set[str]:
    """Show the menu and return the set of services to configure.

    Requires an explicit choice — empty input re-prompts rather than defaulting
    to a destructive "all" path.
    """
    while True:
        choice = input("Configure [b]luesky, [c]omfy-cloud, or [a]ll? ").strip().lower()
        if choice in ("b", "bluesky"):
            return {"bluesky"}
        if choice in ("c", "comfy", "comfy-cloud"):
            return {"comfy_cloud"}
        if choice in ("a", "all"):
            return {"bluesky", "comfy_cloud"}
        print("  Please enter 'b', 'c', or 'a'.", file=sys.stderr)


def _auth(args: argparse.Namespace) -> None:
    """Configure Bluesky and/or Comfy Cloud credentials with merge semantics.

    Reads the existing credentials.json first, updates only the selected
    service(s), and writes back atomically — unrelated blocks (existing or
    future) are preserved. EOF on any prompt exits 1 cleanly instead of
    surfacing a Python traceback.
    """
    try:
        _run_auth(args)
    except EOFError:
        print("\nError: no input available (stdin closed).", file=sys.stderr)
        sys.exit(1)


def _run_auth(args: argparse.Namespace) -> None:
    # Resolve which services to configure from flags or an interactive menu.
    services: set[str] = set()
    if getattr(args, "all", False):
        services = {"bluesky", "comfy_cloud"}
    else:
        if getattr(args, "bluesky", False):
            services.add("bluesky")
        if getattr(args, "comfy_cloud", False):
            services.add("comfy_cloud")
    if not services:
        services = _pick_services_interactive()

    credentials = _load_existing_credentials()
    changed: list[str] = []

    if "bluesky" in services:
        if credentials.get("bluesky") and not _confirm_overwrite("Bluesky"):
            print("Skipped Bluesky.")
        else:
            credentials["bluesky"] = _prompt_bluesky()
            changed.append("Bluesky")

    if "comfy_cloud" in services:
        if credentials.get("comfy_cloud") and not _confirm_overwrite("Comfy Cloud"):
            print("Skipped Comfy Cloud.")
        else:
            credentials["comfy_cloud"] = _prompt_comfy_cloud()
            changed.append("Comfy Cloud")

    if not changed:
        print("No changes; credentials file left untouched.")
        return

    _write_credentials(credentials)
    print(f"✓ Credentials saved to {CREDENTIALS_FILE} (updated: {', '.join(changed)})")
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


def _resolve_comfyui_cmd() -> str:
    """Resolve ComfyUI start command using the same flow as init.

    Interactive (TTY): prompts for ComfyUI dir + venv, saves to config.toml.
    Non-interactive: uses saved config, auto-detection, or placeholder.
    """
    from slop_studio.init import (
        _detect_comfyui_dir,
        _detect_comfyui_start_cmd,
        _load_config_toml,
        _prompt_comfyui_setup,
    )

    saved_config = _load_config_toml()
    detected_dir = _detect_comfyui_dir()

    if sys.stdin.isatty():
        if detected_dir:
            print(f"  Found ComfyUI at {detected_dir}", file=sys.stderr)
        else:
            print("  ComfyUI not found on PATH or in common locations.", file=sys.stderr)
        cmd = _prompt_comfyui_setup(saved_config, detected_dir)
        if cmd:
            return cmd

    # Non-interactive or prompt returned nothing
    saved_cmd = saved_config.get("comfyui_start_cmd", "")
    if saved_cmd:
        return saved_cmd

    detected = _detect_comfyui_start_cmd()
    if detected:
        return detected

    return "python3 /path/to/ComfyUI/main.py"


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
    comfyui_cmd = _resolve_comfyui_cmd()

    config = {
        "mcpServers": {
            "slop-studio": {
                "command": command,
                "args": [*extra_args, "serve"],
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
    auth_parser = subparsers.add_parser(
        "auth",
        help="Configure Bluesky and/or Comfy Cloud credentials",
    )
    auth_parser.add_argument("--bluesky", action="store_true", help="Configure Bluesky credentials only")
    auth_parser.add_argument(
        "--comfy-cloud",
        dest="comfy_cloud",
        action="store_true",
        help="Configure Comfy Cloud API key (also required for local partner-API templates)",
    )
    auth_parser.add_argument("--all", action="store_true", help="Configure both Bluesky and Comfy Cloud")

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
