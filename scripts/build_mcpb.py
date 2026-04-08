"""Build a .mcpb Desktop Extension package for slop-studio.

Thin wrapper for standalone/CI use. Core logic lives in slop_studio.mcpb.
"""

import sys
from pathlib import Path

# Ensure the project root is on sys.path so slop_studio is importable
# when running this script directly (e.g., `python scripts/build_mcpb.py`).
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from slop_studio.mcpb import build_mcpb  # noqa: E402


def main() -> None:
    """CLI entry point for standalone use."""
    output_dir = Path.cwd()
    output_path = build_mcpb(project_root, output_dir)
    print(output_path)


if __name__ == "__main__":
    main()
