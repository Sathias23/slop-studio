import json
from pathlib import Path
from unittest.mock import patch

import pytest

from slop_studio.init import (
    init_project, _detect_comfyui_start_cmd, _save_to_config_toml,
    _read_saved_comfyui_cmd, _prompt_comfyui_cmd, ASSETS_DIR,
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
    with patch("shutil.which", return_value=None), \
         patch("slop_studio.init._COMFYUI_SEARCH_PATHS", search_paths):
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
    with patch("shutil.which", return_value=None), \
         patch("slop_studio.init._COMFYUI_SEARCH_PATHS", search_paths):
        cmd = _detect_comfyui_start_cmd()
    assert str(venv_python) in cmd
    assert str(comfyui_dir / "main.py") in cmd


def test_init_falls_back_to_placeholder_when_not_found(tmp_path):
    with patch("shutil.which", return_value=None), \
         patch("slop_studio.init._COMFYUI_SEARCH_PATHS", []), \
         patch("slop_studio.init._read_saved_comfyui_cmd", return_value=""), \
         patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = False
        init_project(tmp_path)
    config = json.loads((tmp_path / ".mcp.json").read_text())
    env = config["mcpServers"]["slop-studio"]["env"]
    assert "python3" in env["COMFYUI_START_CMD"]
    assert "~/ComfyUI/main.py" in env["COMFYUI_START_CMD"]


def test_init_uses_detected_comfyui_cmd(tmp_path):
    with patch("slop_studio.init._detect_comfyui_start_cmd", return_value="comfyui"):
        init_project(tmp_path)
    config = json.loads((tmp_path / ".mcp.json").read_text())
    env = config["mcpServers"]["slop-studio"]["env"]
    assert env["COMFYUI_START_CMD"] == "comfyui"


# --- Interactive prompt tests ---


def test_prompt_uses_user_input():
    with patch("builtins.input", return_value="python3 /my/ComfyUI/main.py"):
        result = _prompt_comfyui_cmd("")
    assert result == "python3 /my/ComfyUI/main.py"


def test_prompt_shows_saved_default_and_accepts_enter():
    with patch("builtins.input", return_value="") as mock_input:
        result = _prompt_comfyui_cmd("python3 ~/ComfyUI/main.py")
    assert result == "python3 ~/ComfyUI/main.py"
    # Verify the prompt text included the default
    prompt_text = mock_input.call_args[0][0]
    assert "python3 ~/ComfyUI/main.py" in prompt_text


def test_prompt_allows_override_of_saved_default():
    with patch("builtins.input", return_value="/opt/comfyui/venv/bin/python /opt/ComfyUI/main.py"):
        result = _prompt_comfyui_cmd("python3 ~/ComfyUI/main.py")
    assert result == "/opt/comfyui/venv/bin/python /opt/ComfyUI/main.py"


def test_save_and_read_config_toml(tmp_path):
    config_file = tmp_path / "config.toml"
    with patch("slop_studio.init._CONFIG_FILE", config_file), \
         patch("slop_studio.init._CONFIG_DIR", tmp_path):
        _save_to_config_toml("comfyui_start_cmd", "python3 ~/ComfyUI/main.py")
        result = _read_saved_comfyui_cmd()
    assert result == "python3 ~/ComfyUI/main.py"


def test_save_preserves_existing_config_keys(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text('output_dir = "/home/user/art/output"\n')
    with patch("slop_studio.init._CONFIG_FILE", config_file), \
         patch("slop_studio.init._CONFIG_DIR", tmp_path):
        _save_to_config_toml("comfyui_start_cmd", "comfyui")
        saved = config_file.read_text()
    assert "output_dir" in saved
    assert "comfyui_start_cmd" in saved


def test_init_prompts_when_tty_and_no_detect(tmp_path):
    with patch("shutil.which", return_value=None), \
         patch("slop_studio.init._COMFYUI_SEARCH_PATHS", []), \
         patch("slop_studio.init._read_saved_comfyui_cmd", return_value=""), \
         patch("sys.stdin") as mock_stdin, \
         patch("builtins.input", return_value="python3 ~/ComfyUI/main.py"), \
         patch("slop_studio.init._save_to_config_toml") as mock_save:
        mock_stdin.isatty.return_value = True
        init_project(tmp_path)
    config = json.loads((tmp_path / ".mcp.json").read_text())
    env = config["mcpServers"]["slop-studio"]["env"]
    assert env["COMFYUI_START_CMD"] == "python3 ~/ComfyUI/main.py"
    mock_save.assert_called_once_with("comfyui_start_cmd", "python3 ~/ComfyUI/main.py")


def test_init_uses_saved_cmd_in_non_tty(tmp_path):
    with patch("shutil.which", return_value=None), \
         patch("slop_studio.init._COMFYUI_SEARCH_PATHS", []), \
         patch("slop_studio.init._read_saved_comfyui_cmd", return_value="python3 ~/ComfyUI/main.py"), \
         patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = False
        init_project(tmp_path)
    config = json.loads((tmp_path / ".mcp.json").read_text())
    env = config["mcpServers"]["slop-studio"]["env"]
    assert env["COMFYUI_START_CMD"] == "python3 ~/ComfyUI/main.py"


def test_init_placeholder_in_non_tty_no_saved(tmp_path):
    with patch("shutil.which", return_value=None), \
         patch("slop_studio.init._COMFYUI_SEARCH_PATHS", []), \
         patch("slop_studio.init._read_saved_comfyui_cmd", return_value=""), \
         patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = False
        init_project(tmp_path)
    config = json.loads((tmp_path / ".mcp.json").read_text())
    env = config["mcpServers"]["slop-studio"]["env"]
    assert "python3" in env["COMFYUI_START_CMD"]
    assert "~/ComfyUI/main.py" in env["COMFYUI_START_CMD"]


def test_init_no_save_when_accepting_saved_default(tmp_path):
    with patch("shutil.which", return_value=None), \
         patch("slop_studio.init._COMFYUI_SEARCH_PATHS", []), \
         patch("slop_studio.init._read_saved_comfyui_cmd", return_value="python3 ~/ComfyUI/main.py"), \
         patch("sys.stdin") as mock_stdin, \
         patch("builtins.input", return_value=""), \
         patch("slop_studio.init._save_to_config_toml") as mock_save:
        mock_stdin.isatty.return_value = True
        init_project(tmp_path)
    # Should not re-save when user just accepts the default
    mock_save.assert_not_called()
