import os
import sys

# Parse --project-dir before any slop_studio import so config.py sees the env vars.
if "--project-dir" in sys.argv:
    _idx = sys.argv.index("--project-dir")
    try:
        _project_dir = sys.argv[_idx + 1]
        os.environ.setdefault("SLOP_STUDIO_OUTPUT_DIR", os.path.join(_project_dir, "output"))
        os.environ.setdefault("SLOP_STUDIO_TEMPLATES_DIR", os.path.join(_project_dir, "templates"))
        del sys.argv[_idx:_idx + 2]
    except IndexError:
        del sys.argv[_idx]
        print("Warning: --project-dir requires a path argument; ignoring", file=sys.stderr)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        from pathlib import Path
        from slop_studio.init import init_project
        target = Path(os.environ["SLOP_ORIG_DIR"]) if "SLOP_ORIG_DIR" in os.environ else Path.cwd()
        success = init_project(target)
        sys.exit(0 if success else 1)
    elif len(sys.argv) > 1 and sys.argv[1] not in ("serve",):
        print(f"Unknown command: {sys.argv[1]}", file=sys.stderr)
        print("Usage: main.py [serve|init]", file=sys.stderr)
        sys.exit(1)
    else:
        from slop_studio.server import mcp
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
