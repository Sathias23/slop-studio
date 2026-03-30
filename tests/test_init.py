import json
from pathlib import Path

import pytest

from slop_studio.init import init_project, ASSETS_DIR


def test_init_creates_templates_dir(tmp_path):
    init_project(tmp_path)
    assert (tmp_path / "templates").is_dir()


def test_init_copies_all_four_starter_templates(tmp_path):
    init_project(tmp_path)
    names = {f.name for f in (tmp_path / "templates").iterdir()}
    assert names == {
        "flux2_klein.json",
        "flux2_klein.meta.json",
        "flux2_klein_ultrawide.json",
        "flux2_klein_ultrawide.meta.json",
    }


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


def test_init_mcp_json_command_is_uv(tmp_path):
    init_project(tmp_path)
    config = json.loads((tmp_path / ".mcp.json").read_text())
    server = config["mcpServers"]["slop-studio"]
    assert server["command"] == "uv"


def test_init_mcp_json_uses_absolute_repo_path(tmp_path):
    init_project(tmp_path)
    config = json.loads((tmp_path / ".mcp.json").read_text())
    args = config["mcpServers"]["slop-studio"]["args"]
    dir_idx = args.index("--directory")
    repo_path = args[dir_idx + 1]
    assert Path(repo_path).is_absolute()


def test_init_mcp_json_includes_main_py(tmp_path):
    init_project(tmp_path)
    config = json.loads((tmp_path / ".mcp.json").read_text())
    args = config["mcpServers"]["slop-studio"]["args"]
    assert "main.py" in args


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
