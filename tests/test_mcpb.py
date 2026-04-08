"""Tests for MCPB packaging — manifest.json validation and build script."""

import json
import tomllib
import zipfile
from pathlib import Path

import pytest

from slop_studio.mcpb import build_mcpb

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def manifest():
    """Load and parse manifest.json."""
    manifest_path = PROJECT_ROOT / "manifest.json"
    return json.loads(manifest_path.read_text())


@pytest.fixture
def pyproject_version():
    """Read version from pyproject.toml."""
    with open(PROJECT_ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]["version"]


def _build(tmp_path):
    """Helper: build .mcpb and return path."""
    return build_mcpb(PROJECT_ROOT, tmp_path)


class TestManifest:
    def test_valid_json(self):
        path = PROJECT_ROOT / "manifest.json"
        data = json.loads(path.read_text())
        assert isinstance(data, dict)

    def test_required_fields(self, manifest):
        for field in ("manifest_version", "name", "version", "server", "user_config"):
            assert field in manifest, f"Missing required field: {field}"

    def test_manifest_version(self, manifest):
        assert manifest["manifest_version"] == "0.4"

    def test_server_type_uv(self, manifest):
        assert manifest["server"]["type"] == "uv"

    def test_mcp_config_structure(self, manifest):
        mcp_config = manifest["server"]["mcp_config"]
        assert mcp_config["command"] == "uv"
        assert isinstance(mcp_config["args"], list)
        assert isinstance(mcp_config["env"], dict)

    def test_mcp_config_env_references_user_config(self, manifest):
        env = manifest["server"]["mcp_config"]["env"]
        assert env["COMFYUI_URL"] == "${user_config.COMFYUI_URL}"
        assert env["COMFYUI_START_CMD"] == "${user_config.COMFYUI_START_CMD}"
        assert env["SLOP_STUDIO_OUTPUT_DIR"] == "${user_config.SLOP_STUDIO_OUTPUT_DIR}"

    def test_user_config_comfyui_url(self, manifest):
        cfg = manifest["user_config"]["COMFYUI_URL"]
        assert cfg["type"] == "string"
        assert cfg["default"] == "http://localhost:8188"

    def test_user_config_comfyui_start_cmd(self, manifest):
        cfg = manifest["user_config"]["COMFYUI_START_CMD"]
        assert cfg["type"] == "string"
        assert cfg["required"] is False

    def test_user_config_output_dir(self, manifest):
        cfg = manifest["user_config"]["SLOP_STUDIO_OUTPUT_DIR"]
        assert cfg["type"] == "directory"

    def test_version_matches_pyproject(self, manifest, pyproject_version):
        assert manifest["version"] == pyproject_version

    def test_tools_declared(self, manifest):
        tool_names = {t["name"] for t in manifest["tools"]}
        expected = {
            "list_templates", "get_template", "queue_prompt",
            "check_next_job", "get_image", "open_image", "open_gallery",
            "post_to_bluesky", "add_template", "update_template", "delete_template",
        }
        assert tool_names == expected


class TestBuildScript:
    def test_produces_valid_zip(self, tmp_path):
        output = _build(tmp_path)
        assert output.exists()
        assert output.suffix == ".mcpb"
        assert zipfile.is_zipfile(output)

    def test_output_filename_contains_version(self, tmp_path, pyproject_version):
        output = _build(tmp_path)
        assert pyproject_version in output.name

    def test_contains_manifest(self, tmp_path):
        output = _build(tmp_path)
        with zipfile.ZipFile(output) as zf:
            assert "manifest.json" in zf.namelist()

    def test_contains_pyproject(self, tmp_path):
        output = _build(tmp_path)
        with zipfile.ZipFile(output) as zf:
            assert "pyproject.toml" in zf.namelist()

    def test_contains_server(self, tmp_path):
        output = _build(tmp_path)
        with zipfile.ZipFile(output) as zf:
            assert "slop_studio/server.py" in zf.namelist()

    def test_contains_templates(self, tmp_path):
        output = _build(tmp_path)
        with zipfile.ZipFile(output) as zf:
            names = zf.namelist()
            template_files = [n for n in names if n.startswith("templates/")]
            assert len(template_files) > 0

    def test_template_pairs_complete(self, tmp_path):
        output = _build(tmp_path)
        with zipfile.ZipFile(output) as zf:
            names = zf.namelist()
            json_templates = {
                n for n in names
                if n.startswith("templates/") and n.endswith(".json") and not n.endswith(".meta.json")
            }
            for t in json_templates:
                meta = t.replace(".json", ".meta.json")
                assert meta in names, f"Missing meta file for {t}"

    def test_excludes_tests(self, tmp_path):
        output = _build(tmp_path)
        with zipfile.ZipFile(output) as zf:
            for name in zf.namelist():
                assert not name.startswith("tests/"), f"tests/ should be excluded: {name}"

    def test_excludes_pycache(self, tmp_path):
        output = _build(tmp_path)
        with zipfile.ZipFile(output) as zf:
            for name in zf.namelist():
                assert "__pycache__" not in name, f"__pycache__ should be excluded: {name}"

    def test_excludes_output_dir(self, tmp_path):
        output = _build(tmp_path)
        with zipfile.ZipFile(output) as zf:
            for name in zf.namelist():
                assert not name.startswith("output/"), f"output/ should be excluded: {name}"

    def test_excludes_git(self, tmp_path):
        output = _build(tmp_path)
        with zipfile.ZipFile(output) as zf:
            for name in zf.namelist():
                assert not name.startswith(".git/"), f".git/ should be excluded: {name}"

    def test_excludes_dev_artifacts(self, tmp_path):
        output = _build(tmp_path)
        with zipfile.ZipFile(output) as zf:
            for name in zf.namelist():
                assert not name.startswith("_bmad-output/")
                assert not name.startswith(".claude/")
                assert not name.startswith(".devcontainer/")

    def test_excludes_mcpb_build_module(self, tmp_path):
        output = _build(tmp_path)
        with zipfile.ZipFile(output) as zf:
            assert "slop_studio/mcpb.py" not in zf.namelist()
