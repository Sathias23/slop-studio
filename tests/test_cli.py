"""Tests for slop_studio.cli — CLI entry point."""

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
