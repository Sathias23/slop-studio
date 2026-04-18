"""Build a .mcpb Desktop Extension package for slop-studio."""

import json
import tomllib
import zipfile
from pathlib import Path

EXCLUDE_DIRS = {
    "tests",
    "output",
    ".git",
    "__pycache__",
    ".venv",
    "_bmad-output",
    ".claude",
    ".devcontainer",
    "scripts",
    ".venv-test",
    "research",
    ".github",
    "docs",
}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def build_mcpb(project_root: Path, output_dir: Path) -> Path:
    """Build a .mcpb Desktop Extension package.

    Args:
        project_root: Root of the slop-studio repository.
        output_dir: Directory to write the .mcpb file to.

    Returns:
        Path to the created .mcpb file.
    """
    with open(project_root / "pyproject.toml", "rb") as f:
        pyproject = tomllib.load(f)
    version = pyproject["project"]["version"]

    # Validate manifest exists and version matches
    manifest_path = project_root / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError("manifest.json not found in project root")

    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    if manifest.get("version") != version:
        raise ValueError(
            f"manifest.json version ({manifest.get('version')}) does not match pyproject.toml version ({version})"
        )

    output_path = output_dir / f"slop-studio-{version}.mcpb"

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add manifest.json
        zf.write(manifest_path, "manifest.json")

        # Add pyproject.toml
        zf.write(project_root / "pyproject.toml", "pyproject.toml")

        # Add slop_studio/ package (exclude build infrastructure)
        EXCLUDE_NAMES = {"mcpb.py"}
        slop_dir = project_root / "slop_studio"
        for p in sorted(slop_dir.rglob("*")):
            if not p.is_file():
                continue
            if p.name in EXCLUDE_NAMES:
                continue
            rel = p.relative_to(project_root)
            if any(part in EXCLUDE_DIRS for part in rel.parts):
                continue
            if p.suffix in EXCLUDE_SUFFIXES:
                continue
            arcname = str(rel)
            zf.write(p, arcname)

    # Validate archive contents
    expected = {"manifest.json", "pyproject.toml"}
    with zipfile.ZipFile(output_path, "r") as zf:
        names = set(zf.namelist())

    missing = expected - names
    if missing:
        output_path.unlink(missing_ok=True)
        raise RuntimeError(f"Archive missing expected files: {missing}")

    has_server = any(n.startswith("slop_studio/") for n in names)
    if not has_server:
        output_path.unlink(missing_ok=True)
        raise RuntimeError("Archive missing slop_studio/ package")

    has_templates = any(n.startswith("slop_studio/assets/starter-templates/") for n in names)
    if not has_templates:
        output_path.unlink(missing_ok=True)
        raise RuntimeError("Archive missing slop_studio/assets/starter-templates/ directory")

    return output_path
