import json
from pathlib import Path
from unittest.mock import patch

from slop_studio.init import (
    ASSETS_DIR,
    _build_start_cmd,
    _detect_comfyui_start_cmd,
    _prompt_comfyui_setup,
    _prompt_path,
    _save_to_config_toml,
    init_project,
)


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
    with patch("shutil.which", return_value=None), patch("slop_studio.init._COMFYUI_SEARCH_PATHS", search_paths):
        cmd = _detect_comfyui_start_cmd()
    # Should not use bare 'python', should use python3 or a venv python
    assert cmd.startswith("python") or cmd.startswith("/") or cmd.startswith("'")
    assert str(comfyui_dir / "main.py") in cmd


def test_init_detects_comfyui_venv_python(tmp_path):
    comfyui_dir = tmp_path / "ComfyUI"
    comfyui_dir.mkdir()
    (comfyui_dir / "main.py").write_text("# ComfyUI")
    venv_bin = comfyui_dir / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    venv_python = venv_bin / "python"
    venv_python.write_text("#!/usr/bin/env python3")
    venv_python.chmod(0o755)
    search_paths = [comfyui_dir]
    with patch("shutil.which", return_value=None), patch("slop_studio.init._COMFYUI_SEARCH_PATHS", search_paths):
        cmd = _detect_comfyui_start_cmd()
    assert str(venv_python) in cmd
    assert str(comfyui_dir / "main.py") in cmd


def test_init_falls_back_to_placeholder_when_not_found(tmp_path):
    with (
        patch("slop_studio.init._detect_comfyui_dir", return_value=None),
        patch("slop_studio.init._load_config_toml", return_value={}),
        patch("shutil.which", return_value=None),
        patch("slop_studio.init._COMFYUI_SEARCH_PATHS", []),
        patch("sys.stdin") as mock_stdin,
    ):
        mock_stdin.isatty.return_value = False
        init_project(tmp_path)
    config = json.loads((tmp_path / ".mcp.json").read_text())
    env = config["mcpServers"]["slop-studio"]["env"]
    assert "python3" in env["COMFYUI_START_CMD"]
    assert "~/ComfyUI/main.py" in env["COMFYUI_START_CMD"]


def test_init_uses_detected_dir_in_non_tty(tmp_path):
    comfyui_dir = tmp_path / "ComfyUI"
    comfyui_dir.mkdir()
    (comfyui_dir / "main.py").write_text("# ComfyUI")
    with (
        patch("slop_studio.init._detect_comfyui_dir", return_value=comfyui_dir),
        patch("slop_studio.init._load_config_toml", return_value={}),
        patch("sys.stdin") as mock_stdin,
    ):
        mock_stdin.isatty.return_value = False
        init_project(tmp_path)
    config = json.loads((tmp_path / ".mcp.json").read_text())
    env = config["mcpServers"]["slop-studio"]["env"]
    assert str(comfyui_dir / "main.py") in env["COMFYUI_START_CMD"]


# --- Interactive prompt tests ---


def test_prompt_path_uses_input():
    with patch("builtins.input", return_value="/my/ComfyUI"):
        result = _prompt_path("ComfyUI directory", "")
    assert result == "/my/ComfyUI"


def test_prompt_path_shows_default_and_accepts_enter():
    with patch("builtins.input", return_value="") as mock_input:
        result = _prompt_path("ComfyUI directory", "/home/user/ComfyUI")
    assert result == "/home/user/ComfyUI"
    prompt_text = mock_input.call_args[0][0]
    assert "/home/user/ComfyUI" in prompt_text


def test_build_start_cmd_with_venv(tmp_path):
    comfyui_dir = tmp_path / "ComfyUI"
    comfyui_dir.mkdir()
    (comfyui_dir / "main.py").write_text("# ComfyUI")
    venv_dir = tmp_path / "myvenv"
    venv_bin = venv_dir / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/bin/sh")
    (venv_bin / "python").chmod(0o755)
    cmd = _build_start_cmd(comfyui_dir, venv_dir)
    assert str(venv_bin / "python") in cmd
    assert str(comfyui_dir / "main.py") in cmd


def test_build_start_cmd_without_venv(tmp_path):
    comfyui_dir = tmp_path / "ComfyUI"
    comfyui_dir.mkdir()
    (comfyui_dir / "main.py").write_text("# ComfyUI")
    cmd = _build_start_cmd(comfyui_dir)
    assert str(comfyui_dir / "main.py") in cmd


def test_save_and_read_config_toml(tmp_path):
    config_file = tmp_path / "config.toml"
    with patch("slop_studio.init._CONFIG_FILE", config_file), patch("slop_studio.init._CONFIG_DIR", tmp_path):
        _save_to_config_toml("comfyui_dir", "/home/user/ComfyUI")
        _save_to_config_toml("comfyui_venv", "/home/user/ComfyUI/venv")
        import tomllib

        with open(config_file, "rb") as f:
            data = tomllib.load(f)
    assert data["comfyui_dir"] == "/home/user/ComfyUI"
    assert data["comfyui_venv"] == "/home/user/ComfyUI/venv"


def test_save_preserves_existing_config_keys(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text('output_dir = "/home/user/art/output"\n')
    with patch("slop_studio.init._CONFIG_FILE", config_file), patch("slop_studio.init._CONFIG_DIR", tmp_path):
        _save_to_config_toml("comfyui_dir", "/home/user/ComfyUI")
        saved = config_file.read_text()
    assert "output_dir" in saved
    assert "comfyui_dir" in saved


def test_prompt_setup_with_venv(tmp_path):
    comfyui_dir = tmp_path / "ComfyUI"
    comfyui_dir.mkdir()
    (comfyui_dir / "main.py").write_text("# ComfyUI")
    venv_bin = comfyui_dir / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/bin/sh")
    (venv_bin / "python").chmod(0o755)

    inputs = iter([str(comfyui_dir), ""])  # accept dir, accept detected venv
    with (
        patch("builtins.input", side_effect=lambda _: next(inputs)),
        patch("slop_studio.init._CONFIG_FILE", tmp_path / "config.toml"),
        patch("slop_studio.init._CONFIG_DIR", tmp_path),
    ):
        cmd = _prompt_comfyui_setup({}, None)
    assert str(venv_bin / "python") in cmd
    assert str(comfyui_dir / "main.py") in cmd


def test_prompt_setup_returns_none_on_empty_dir():
    with patch("builtins.input", return_value=""):
        result = _prompt_comfyui_setup({}, None)
    assert result is None


def test_init_prompts_when_tty(tmp_path):
    with (
        patch("slop_studio.init._detect_comfyui_dir", return_value=None),
        patch("sys.stdin") as mock_stdin,
        patch("slop_studio.init._prompt_comfyui_setup", return_value="python3 ~/ComfyUI/main.py") as mock_prompt,
    ):
        mock_stdin.isatty.return_value = True
        init_project(tmp_path)
    mock_prompt.assert_called_once()
    config = json.loads((tmp_path / ".mcp.json").read_text())
    env = config["mcpServers"]["slop-studio"]["env"]
    assert env["COMFYUI_START_CMD"] == "python3 ~/ComfyUI/main.py"


def test_init_uses_saved_cmd_in_non_tty(tmp_path):
    with (
        patch("slop_studio.init._detect_comfyui_dir", return_value=None),
        patch("slop_studio.init._load_config_toml", return_value={"comfyui_start_cmd": "python3 ~/ComfyUI/main.py"}),
        patch("sys.stdin") as mock_stdin,
    ):
        mock_stdin.isatty.return_value = False
        init_project(tmp_path)
    config = json.loads((tmp_path / ".mcp.json").read_text())
    env = config["mcpServers"]["slop-studio"]["env"]
    assert env["COMFYUI_START_CMD"] == "python3 ~/ComfyUI/main.py"


def test_init_placeholder_in_non_tty_no_saved(tmp_path):
    with (
        patch("slop_studio.init._detect_comfyui_dir", return_value=None),
        patch("slop_studio.init._load_config_toml", return_value={}),
        patch("shutil.which", return_value=None),
        patch("slop_studio.init._COMFYUI_SEARCH_PATHS", []),
        patch("sys.stdin") as mock_stdin,
    ):
        mock_stdin.isatty.return_value = False
        init_project(tmp_path)
    config = json.loads((tmp_path / ".mcp.json").read_text())
    env = config["mcpServers"]["slop-studio"]["env"]
    assert "python3" in env["COMFYUI_START_CMD"]
    assert "~/ComfyUI/main.py" in env["COMFYUI_START_CMD"]
