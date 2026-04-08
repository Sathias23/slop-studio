"""Backwards-compatible entry point.

Existing .mcp.json files reference ``uv run main.py``.  This shim
delegates to the proper CLI so those configs keep working.
"""

import os
import sys

# Parse --project-dir before any slop_studio import so config.py sees the env vars.
if "--project-dir" in sys.argv:
    _idx = sys.argv.index("--project-dir")
    try:
        _project_dir = sys.argv[_idx + 1]
        os.environ.setdefault("SLOP_STUDIO_OUTPUT_DIR", os.path.join(_project_dir, "output"))
        os.environ.setdefault("SLOP_STUDIO_TEMPLATES_DIR", os.path.join(_project_dir, "templates"))
        from dotenv import load_dotenv

        load_dotenv(os.path.join(_project_dir, ".env"))
        # Rewrite argv so argparse in cli.main() sees: serve --project-dir <path>
        sys.argv = [sys.argv[0], "serve", "--project-dir", _project_dir, *sys.argv[_idx + 2 :]]
    except IndexError:
        del sys.argv[_idx]
        print("Warning: --project-dir requires a path argument; ignoring", file=sys.stderr)


def main():
    from slop_studio.cli import main as cli_main

    # If no subcommand given and invoked as main.py, default to serve
    if len(sys.argv) < 2 or sys.argv[1] not in ("auth", "init", "serve"):
        sys.argv.insert(1, "serve")
    cli_main()


if __name__ == "__main__":
    main()
