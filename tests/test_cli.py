"""Tests for slop_studio.cli — CLI entry point."""

import argparse
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from slop_studio.cli import CREDENTIALS_FILE, _auth, main


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """Redirect credentials to a temp directory."""
    creds_file = tmp_path / "credentials.json"
    monkeypatch.setattr("slop_studio.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("slop_studio.cli.CREDENTIALS_FILE", creds_file)
    return tmp_path, creds_file


class TestAuth:
    def test_auth_saves_credentials(self, config_dir):
        _, creds_file = config_dir
        ns = type("NS", (), {"command": "auth"})()
        with patch("builtins.input", return_value="me.bsky.social"), \
             patch("getpass.getpass", return_value="xxxx-yyyy"):
            _auth(ns)

        data = json.loads(creds_file.read_text())
        assert data["bluesky"]["handle"] == "me.bsky.social"
        assert data["bluesky"]["app_password"] == "xxxx-yyyy"

    def test_auth_sets_file_permissions(self, config_dir):
        _, creds_file = config_dir
        ns = type("NS", (), {"command": "auth"})()
        with patch("builtins.input", return_value="me.bsky.social"), \
             patch("getpass.getpass", return_value="xxxx-yyyy"):
            _auth(ns)

        mode = creds_file.stat().st_mode & 0o777
        assert mode == 0o600

    def test_auth_overwrite_confirmed(self, config_dir):
        _, creds_file = config_dir
        creds_file.write_text(json.dumps({"bluesky": {"handle": "old", "app_password": "old"}}))

        ns = type("NS", (), {"command": "auth"})()
        # First input is overwrite confirmation, then handle
        inputs = iter(["y", "new.bsky.social"])
        with patch("builtins.input", side_effect=inputs), \
             patch("getpass.getpass", return_value="new-pass"):
            _auth(ns)

        data = json.loads(creds_file.read_text())
        assert data["bluesky"]["handle"] == "new.bsky.social"

    def test_auth_overwrite_declined(self, config_dir, capsys):
        _, creds_file = config_dir
        original = {"bluesky": {"handle": "old", "app_password": "old"}}
        creds_file.write_text(json.dumps(original))

        ns = type("NS", (), {"command": "auth"})()
        with patch("builtins.input", return_value="n"):
            _auth(ns)

        assert json.loads(creds_file.read_text()) == original
        assert "Aborted" in capsys.readouterr().out

    def test_auth_empty_handle_exits(self, config_dir):
        ns = type("NS", (), {"command": "auth"})()
        with patch("builtins.input", return_value=""), \
             pytest.raises(SystemExit, match="1"):
            _auth(ns)

    def test_auth_empty_password_exits(self, config_dir):
        ns = type("NS", (), {"command": "auth"})()
        with patch("builtins.input", return_value="me.bsky.social"), \
             patch("getpass.getpass", return_value=""), \
             pytest.raises(SystemExit, match="1"):
            _auth(ns)


class TestMain:
    def test_no_args_shows_help(self, capsys):
        with patch("sys.argv", ["slop-studio"]), \
             pytest.raises(SystemExit, match="1"):
            main()

    def test_auth_subcommand(self, config_dir):
        _, creds_file = config_dir
        with patch("sys.argv", ["slop-studio", "auth"]), \
             patch("builtins.input", return_value="me.bsky.social"), \
             patch("getpass.getpass", return_value="xxxx-yyyy"):
            main()
        assert creds_file.exists()

    def test_init_subcommand(self, tmp_path):
        with patch("sys.argv", ["slop-studio", "init", str(tmp_path)]), \
             pytest.raises(SystemExit, match="0"):
            main()
        assert (tmp_path / ".mcp.json").exists()


class TestBuildMcpb:
    def test_build_mcpb_subcommand(self, tmp_path):
        with patch("sys.argv", ["slop-studio", "build-mcpb", "--output-dir", str(tmp_path)]):
            main()
        mcpb_files = list(tmp_path.glob("*.mcpb"))
        assert len(mcpb_files) == 1
        assert "slop-studio-" in mcpb_files[0].name

    def test_build_mcpb_prints_path(self, tmp_path, capsys):
        with patch("sys.argv", ["slop-studio", "build-mcpb", "--output-dir", str(tmp_path)]):
            main()
        captured = capsys.readouterr()
        assert ".mcpb" in captured.out


class TestDesktopConfig:
    def test_outputs_valid_json(self, capsys):
        from slop_studio.cli import _desktop_config

        args = argparse.Namespace(copy=False)
        _desktop_config(args)
        captured = capsys.readouterr()
        config = json.loads(captured.out)
        assert "mcpServers" in config
        assert "slop-studio" in config["mcpServers"]

    def test_includes_command_and_args(self, capsys):
        from slop_studio.cli import _desktop_config

        args = argparse.Namespace(copy=False)
        _desktop_config(args)
        captured = capsys.readouterr()
        config = json.loads(captured.out)
        server = config["mcpServers"]["slop-studio"]
        assert "command" in server
        assert server["args"][-1] == "serve"

    def test_includes_env_vars(self, capsys):
        from slop_studio.cli import _desktop_config

        args = argparse.Namespace(copy=False)
        _desktop_config(args)
        captured = capsys.readouterr()
        config = json.loads(captured.out)
        env = config["mcpServers"]["slop-studio"]["env"]
        assert "COMFYUI_URL" in env
        assert "COMFYUI_START_CMD" in env
        assert "SLOP_STUDIO_OUTPUT_DIR" in env

    def test_detect_slop_studio_path_finds_binary(self, monkeypatch):
        from slop_studio.cli import _detect_slop_studio_path

        monkeypatch.setattr("shutil.which", lambda x: "/usr/local/bin/slop-studio" if x == "slop-studio" else None)
        command, extra = _detect_slop_studio_path()
        assert command == "/usr/local/bin/slop-studio"
        assert extra == []

    def test_detect_slop_studio_path_fallback(self, monkeypatch):
        from slop_studio.cli import _detect_slop_studio_path

        monkeypatch.setattr("shutil.which", lambda x: None)
        command, extra = _detect_slop_studio_path()
        assert command == "uv"
        assert "slop-studio" in extra

    def test_resolve_comfyui_cmd_found(self, tmp_path, monkeypatch):
        from slop_studio.cli import _resolve_comfyui_cmd
        from unittest.mock import patch

        comfyui_dir = tmp_path / "ComfyUI"
        comfyui_dir.mkdir()
        main_py = comfyui_dir / "main.py"
        main_py.write_text("# ComfyUI")
        with patch("sys.stdin") as mock_stdin, \
             patch("slop_studio.init._COMFYUI_SEARCH_PATHS", [comfyui_dir]), \
             patch("slop_studio.init._load_config_toml", return_value={}):
            mock_stdin.isatty.return_value = False
            result = _resolve_comfyui_cmd()
        assert str(main_py) in result
        assert "python" in result

    def test_resolve_comfyui_cmd_not_found(self, tmp_path, monkeypatch):
        from slop_studio.cli import _resolve_comfyui_cmd
        from unittest.mock import patch

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        with patch("sys.stdin") as mock_stdin, \
             patch("slop_studio.init._load_config_toml", return_value={}):
            mock_stdin.isatty.return_value = False
            result = _resolve_comfyui_cmd()
        assert "/path/to/" in result

    def test_copy_flag_attempts_clipboard(self, capsys, monkeypatch):
        from slop_studio.cli import _desktop_config

        calls = []

        def mock_which(name):
            if name == "slop-studio":
                return "/usr/bin/slop-studio"
            if name == "pbcopy":
                return "/usr/bin/pbcopy"
            return None

        monkeypatch.setattr("shutil.which", mock_which)

        with patch("subprocess.run") as mock_run:
            args = argparse.Namespace(copy=True)
            _desktop_config(args)
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert call_args[0][0] == ["pbcopy"]

    def test_desktop_config_subcommand(self, capsys):
        with patch("sys.argv", ["slop-studio", "desktop-config"]):
            main()
        captured = capsys.readouterr()
        config = json.loads(captured.out)
        assert "mcpServers" in config
