import json
from pathlib import Path
from unittest.mock import patch

import pytest

from slop_studio.init import init_project, _detect_comfyui_start_cmd, ASSETS_DIR


def test_init_creates_templates_dir(tmp_path):
    init_project(tmp_path)
    assert (tmp_path / "templates").is_dir()


def test_init_copies_all_starter_templates(tmp_path):
    init_project(tmp_path)
    copied = {f.name for f in (tmp_path / "templates").iterdir()}
    originals = {f.name for f in (ASSETS_DIR / "starter-templates").iterdir()}
    assert copied == originals


def test_init_templates_are_identical_to_originals(tmp_path):
    init_project(tmp_path)
    src = ASSETS_DIR / "starter-templates" / "flux2_klein.meta.json"
    dst = tmp_path / "templates" / "flux2_klein.meta.json"
    assert dst.read_bytes() == src.read_bytes()


def test_init_creates_mcp_json(tmp_path):
    init_project(tmp_path)
    mcp_json = tmp_path / ".mcp.json"
    assert mcp_json.is_file()
    config = json.loads(mcp_json.read_text())
    assert "mcpServers" in config
    assert "slop-studio" in config["mcpServers"]


def test_init_mcp_json_command_is_slop_studio(tmp_path):
    init_project(tmp_path)
    config = json.loads((tmp_path / ".mcp.json").read_text())
    server = config["mcpServers"]["slop-studio"]
    assert server["command"] == "slop-studio"


def test_init_mcp_json_uses_serve_subcommand(tmp_path):
    init_project(tmp_path)
    config = json.loads((tmp_path / ".mcp.json").read_text())
    args = config["mcpServers"]["slop-studio"]["args"]
    assert args[0] == "serve"


def test_init_mcp_json_includes_project_dir(tmp_path):
    init_project(tmp_path)
    config = json.loads((tmp_path / ".mcp.json").read_text())
    args = config["mcpServers"]["slop-studio"]["args"]
    assert "--project-dir" in args
    dir_idx = args.index("--project-dir")
    assert Path(args[dir_idx + 1]).is_absolute()


def test_init_creates_claude_commands_dir(tmp_path):
    init_project(tmp_path)
    assert (tmp_path / ".claude" / "commands").is_dir()


def test_init_copies_generate_command(tmp_path):
    init_project(tmp_path)
    assert (tmp_path / ".claude" / "commands" / "generate.md").is_file()


def test_init_creates_claude_md(tmp_path):
    init_project(tmp_path)
    assert (tmp_path / "CLAUDE.md").is_file()


def test_init_skips_existing_mcp_json(tmp_path):
    existing = {"custom": "preserved"}
    (tmp_path / ".mcp.json").write_text(json.dumps(existing))
    init_project(tmp_path)
    assert json.loads((tmp_path / ".mcp.json").read_text()) == existing


def test_init_skips_existing_claude_md(tmp_path):
    original = "# My Custom CLAUDE.md"
    (tmp_path / "CLAUDE.md").write_text(original)
    init_project(tmp_path)
    assert (tmp_path / "CLAUDE.md").read_text() == original


def test_init_idempotent_second_run(tmp_path):
    init_project(tmp_path)
    result = init_project(tmp_path)
    assert result is True


def test_init_returns_true_on_success(tmp_path):
    assert init_project(tmp_path) is True


def test_init_mcp_json_includes_comfyui_start_cmd(tmp_path):
    init_project(tmp_path)
    config = json.loads((tmp_path / ".mcp.json").read_text())
    env = config["mcpServers"]["slop-studio"]["env"]
    assert "COMFYUI_START_CMD" in env
    assert env["COMFYUI_START_CMD"] != ""


def test_init_detects_comfyui_on_path(tmp_path):
    with patch("shutil.which", return_value="/usr/local/bin/comfyui"):
        cmd = _detect_comfyui_start_cmd()
    assert cmd == "comfyui"


def test_init_detects_comfyui_main_py(tmp_path):
    comfyui_dir = tmp_path / "ComfyUI"
    comfyui_dir.mkdir()
    (comfyui_dir / "main.py").write_text("# ComfyUI")
    search_paths = [comfyui_dir]
    with patch("shutil.which", return_value=None), \
         patch("slop_studio.init._COMFYUI_SEARCH_PATHS", search_paths):
        cmd = _detect_comfyui_start_cmd()
    assert cmd == f"python {comfyui_dir / 'main.py'}"


def test_init_falls_back_to_placeholder_when_not_found(tmp_path):
    with patch("shutil.which", return_value=None), \
         patch("slop_studio.init._COMFYUI_SEARCH_PATHS", []):
        init_project(tmp_path)
    config = json.loads((tmp_path / ".mcp.json").read_text())
    env = config["mcpServers"]["slop-studio"]["env"]
    assert env["COMFYUI_START_CMD"] == "python ~/ComfyUI/main.py"


def test_init_uses_detected_comfyui_cmd(tmp_path):
    with patch("slop_studio.init._detect_comfyui_start_cmd", return_value="comfyui"):
        init_project(tmp_path)
    config = json.loads((tmp_path / ".mcp.json").read_text())
    env = config["mcpServers"]["slop-studio"]["env"]
    assert env["COMFYUI_START_CMD"] == "comfyui"
