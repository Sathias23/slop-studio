import sys


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        import os
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
