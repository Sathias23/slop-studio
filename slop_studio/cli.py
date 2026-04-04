"""CLI entry point for slop-studio."""

import argparse
import getpass
import json
import os
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

    args = parser.parse_args()

    if args.command == "auth":
        _auth(args)
    elif args.command == "init":
        _init(args)
    elif args.command == "serve":
        _serve(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
