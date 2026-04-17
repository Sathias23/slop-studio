"""Tests for slop_studio.cli — CLI entry point."""

import argparse
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from slop_studio.cli import _auth, main


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """Redirect credentials to a temp directory."""
    creds_file = tmp_path / "credentials.json"
    monkeypatch.setattr("slop_studio.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("slop_studio.cli.CREDENTIALS_FILE", creds_file)
    return tmp_path, creds_file


def _ns(**overrides):
    """Build an auth argparse Namespace with explicit service-flag defaults."""
    defaults = {"command": "auth", "bluesky": False, "comfy_cloud": False, "all": False}
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestAuth:
    def test_bluesky_flag_saves_only_bluesky(self, config_dir):
        _, creds_file = config_dir
        with patch("builtins.input", return_value="me.bsky.social"), patch("getpass.getpass", return_value="xxxx-yyyy"):
            _auth(_ns(bluesky=True))

        data = json.loads(creds_file.read_text())
        assert data == {"bluesky": {"handle": "me.bsky.social", "app_password": "xxxx-yyyy"}}

    def test_comfy_cloud_flag_saves_only_comfy_cloud(self, config_dir):
        _, creds_file = config_dir
        with patch("getpass.getpass", return_value="comfy_abcdef"):
            _auth(_ns(comfy_cloud=True))

        data = json.loads(creds_file.read_text())
        assert data == {"comfy_cloud": {"api_key": "comfy_abcdef"}}

    def test_all_flag_saves_both_services(self, config_dir):
        _, creds_file = config_dir
        with (
            patch("builtins.input", return_value="me.bsky.social"),
            patch("getpass.getpass", side_effect=["xxxx-yyyy", "comfy_abcdef"]),
        ):
            _auth(_ns(all=True))

        data = json.loads(creds_file.read_text())
        assert data["bluesky"] == {"handle": "me.bsky.social", "app_password": "xxxx-yyyy"}
        assert data["comfy_cloud"] == {"api_key": "comfy_abcdef"}

    def test_file_permissions_are_0600(self, config_dir):
        _, creds_file = config_dir
        with patch("getpass.getpass", return_value="comfy_abcdef"):
            _auth(_ns(comfy_cloud=True))

        mode = creds_file.stat().st_mode & 0o777
        assert mode == 0o600

    def test_comfy_cloud_flag_preserves_existing_bluesky(self, config_dir):
        _, creds_file = config_dir
        original_bsky = {"handle": "original", "app_password": "original-pass"}
        creds_file.write_text(json.dumps({"bluesky": original_bsky}))

        with patch("getpass.getpass", return_value="comfy_newkey"):
            _auth(_ns(comfy_cloud=True))

        data = json.loads(creds_file.read_text())
        assert data["bluesky"] == original_bsky
        assert data["comfy_cloud"] == {"api_key": "comfy_newkey"}

    def test_bluesky_flag_preserves_existing_comfy_cloud(self, config_dir):
        _, creds_file = config_dir
        original_cloud = {"api_key": "comfy_existing"}
        creds_file.write_text(json.dumps({"comfy_cloud": original_cloud}))

        with patch("builtins.input", return_value="me.bsky.social"), patch("getpass.getpass", return_value="xxxx-yyyy"):
            _auth(_ns(bluesky=True))

        data = json.loads(creds_file.read_text())
        assert data["comfy_cloud"] == original_cloud
        assert data["bluesky"] == {"handle": "me.bsky.social", "app_password": "xxxx-yyyy"}

    def test_unrelated_top_level_keys_preserved(self, config_dir):
        _, creds_file = config_dir
        creds_file.write_text(
            json.dumps({"bluesky": {"handle": "h", "app_password": "p"}, "other_future_block": {"x": 1}})
        )

        with patch("getpass.getpass", return_value="comfy_newkey"):
            _auth(_ns(comfy_cloud=True))

        data = json.loads(creds_file.read_text())
        assert data["other_future_block"] == {"x": 1}

    def test_interactive_menu_routes_to_bluesky(self, config_dir):
        _, creds_file = config_dir
        with (
            patch("builtins.input", side_effect=["b", "me.bsky.social"]),
            patch("getpass.getpass", return_value="xxxx-yyyy"),
        ):
            _auth(_ns())

        data = json.loads(creds_file.read_text())
        assert "bluesky" in data
        assert "comfy_cloud" not in data

    def test_interactive_menu_routes_to_comfy_cloud(self, config_dir):
        _, creds_file = config_dir
        with patch("builtins.input", return_value="c"), patch("getpass.getpass", return_value="comfy_abc"):
            _auth(_ns())

        data = json.loads(creds_file.read_text())
        assert data == {"comfy_cloud": {"api_key": "comfy_abc"}}

    def test_interactive_menu_retries_on_invalid_choice(self, config_dir):
        _, creds_file = config_dir
        with (
            patch("builtins.input", side_effect=["q", "xxx", "c"]),
            patch("getpass.getpass", return_value="comfy_abc"),
        ):
            _auth(_ns())

        assert json.loads(creds_file.read_text()) == {"comfy_cloud": {"api_key": "comfy_abc"}}

    def test_overwrite_confirmation_accepts(self, config_dir):
        _, creds_file = config_dir
        creds_file.write_text(json.dumps({"bluesky": {"handle": "old", "app_password": "old"}}))

        with (
            patch("builtins.input", side_effect=["y", "new.bsky.social"]),
            patch("getpass.getpass", return_value="new-pass"),
        ):
            _auth(_ns(bluesky=True))

        assert json.loads(creds_file.read_text())["bluesky"]["handle"] == "new.bsky.social"

    def test_overwrite_confirmation_declines_keeps_existing(self, config_dir, capsys):
        _, creds_file = config_dir
        original = {"bluesky": {"handle": "old", "app_password": "old"}}
        creds_file.write_text(json.dumps(original))

        with patch("builtins.input", return_value="n"):
            _auth(_ns(bluesky=True))

        assert json.loads(creds_file.read_text())["bluesky"] == original["bluesky"]
        assert "Skipped Bluesky" in capsys.readouterr().out

    def test_malformed_json_exits_and_does_not_modify(self, config_dir, capsys):
        _, creds_file = config_dir
        creds_file.write_text("not valid json {")
        original_bytes = creds_file.read_bytes()

        with pytest.raises(SystemExit, match="1"):
            _auth(_ns(bluesky=True))

        assert creds_file.read_bytes() == original_bytes
        err = capsys.readouterr().err
        assert "not valid JSON" in err
        assert str(creds_file) in err

    def test_empty_bluesky_handle_exits(self, config_dir):
        with patch("builtins.input", return_value=""), pytest.raises(SystemExit, match="1"):
            _auth(_ns(bluesky=True))

    def test_empty_bluesky_password_exits(self, config_dir):
        with (
            patch("builtins.input", return_value="me.bsky.social"),
            patch("getpass.getpass", return_value=""),
            pytest.raises(SystemExit, match="1"),
        ):
            _auth(_ns(bluesky=True))

    def test_empty_comfy_cloud_key_exits(self, config_dir):
        with patch("getpass.getpass", return_value=""), pytest.raises(SystemExit, match="1"):
            _auth(_ns(comfy_cloud=True))

    # --- Review-driven patches: coverage for the hardening -----------------

    @pytest.mark.parametrize(
        "flag, inputs, secrets",
        [
            ("bluesky", ["me.bsky.social"], ["xxxx-yyyy"]),
            ("comfy_cloud", [], ["comfy_abcdef"]),
            ("all", ["me.bsky.social"], ["xxxx-yyyy", "comfy_abcdef"]),
        ],
    )
    def test_file_mode_is_0600_for_every_flag_path(self, config_dir, flag, inputs, secrets):
        _, creds_file = config_dir
        with (
            patch("builtins.input", side_effect=inputs or [""]),
            patch("getpass.getpass", side_effect=secrets),
        ):
            _auth(_ns(**{flag: True}))
        assert creds_file.stat().st_mode & 0o777 == 0o600

    def test_existing_file_with_loose_mode_is_tightened_to_0600(self, config_dir):
        _, creds_file = config_dir
        creds_file.write_text(json.dumps({"bluesky": {"handle": "h", "app_password": "p"}}))
        creds_file.chmod(0o644)

        with patch("getpass.getpass", return_value="comfy_abc"):
            _auth(_ns(comfy_cloud=True))

        assert creds_file.stat().st_mode & 0o777 == 0o600

    def test_comfy_cloud_overwrite_confirmation_accepts(self, config_dir):
        _, creds_file = config_dir
        creds_file.write_text(json.dumps({"comfy_cloud": {"api_key": "old_key"}}))

        with patch("builtins.input", return_value="y"), patch("getpass.getpass", return_value="new_key"):
            _auth(_ns(comfy_cloud=True))

        assert json.loads(creds_file.read_text())["comfy_cloud"] == {"api_key": "new_key"}

    def test_comfy_cloud_overwrite_confirmation_declines_keeps_existing(self, config_dir, capsys):
        _, creds_file = config_dir
        original = {"comfy_cloud": {"api_key": "old_key"}}
        creds_file.write_text(json.dumps(original))

        with patch("builtins.input", return_value="n"):
            _auth(_ns(comfy_cloud=True))

        assert json.loads(creds_file.read_text()) == original
        assert "Skipped Comfy Cloud" in capsys.readouterr().out

    def test_overwrite_default_is_no(self, config_dir):
        """Empty input at the [y/N] prompt must decline, not accept."""
        _, creds_file = config_dir
        original = {"bluesky": {"handle": "old", "app_password": "old"}}
        creds_file.write_text(json.dumps(original))

        with patch("builtins.input", return_value=""):
            _auth(_ns(bluesky=True))

        assert json.loads(creds_file.read_text()) == original

    def test_menu_empty_input_reprompts_instead_of_defaulting_to_all(self, config_dir):
        _, creds_file = config_dir
        with (
            patch("builtins.input", side_effect=["", "  ", "b", "me.bsky.social"]),
            patch("getpass.getpass", return_value="xxxx-yyyy"),
        ):
            _auth(_ns())

        data = json.loads(creds_file.read_text())
        assert "bluesky" in data
        assert "comfy_cloud" not in data

    def test_fresh_install_menu_all_writes_both_with_0600(self, config_dir):
        """Combines: no existing file + interactive [a]ll menu + both blocks + mode 0600."""
        _, creds_file = config_dir
        assert not creds_file.exists()
        with (
            patch("builtins.input", side_effect=["a", "me.bsky.social"]),
            patch("getpass.getpass", side_effect=["xxxx-yyyy", "comfy_abcdef"]),
        ):
            _auth(_ns())

        data = json.loads(creds_file.read_text())
        assert "bluesky" in data and "comfy_cloud" in data
        assert creds_file.stat().st_mode & 0o777 == 0o600

    def test_all_flag_exits_on_empty_bluesky_handle_before_writing(self, config_dir):
        _, creds_file = config_dir
        with patch("builtins.input", return_value=""), pytest.raises(SystemExit, match="1"):
            _auth(_ns(all=True))
        assert not creds_file.exists()

    def test_all_flag_exits_on_empty_comfy_key_after_bluesky_success(self, config_dir):
        """When --all is used and the second prompt fails, the file must not exist.

        The Bluesky block was captured in memory but `_write_credentials` is never
        reached — so on-disk state stays untouched.
        """
        _, creds_file = config_dir
        with (
            patch("builtins.input", return_value="me.bsky.social"),
            patch("getpass.getpass", side_effect=["xxxx-yyyy", ""]),
            pytest.raises(SystemExit, match="1"),
        ):
            _auth(_ns(all=True))
        assert not creds_file.exists()

    @pytest.mark.parametrize("bad_value", ["[]", '"just a string"', "null", "42"])
    def test_non_dict_top_level_json_rejected(self, config_dir, capsys, bad_value):
        _, creds_file = config_dir
        creds_file.write_text(bad_value)
        original = creds_file.read_bytes()

        with pytest.raises(SystemExit, match="1"):
            _auth(_ns(bluesky=True))

        assert creds_file.read_bytes() == original
        assert "must be an object" in capsys.readouterr().err

    def test_non_dict_service_block_rejected(self, config_dir, capsys):
        _, creds_file = config_dir
        creds_file.write_text(json.dumps({"bluesky": "legacy_string_form"}))
        original = creds_file.read_bytes()

        with pytest.raises(SystemExit, match="1"):
            _auth(_ns(comfy_cloud=True))

        assert creds_file.read_bytes() == original
        err = capsys.readouterr().err
        assert "'bluesky' must be an object" in err

    def test_declining_overwrite_does_not_rewrite_file(self, config_dir, tmp_path):
        """If the user declines the only requested service, the file's mtime
        stays the same — we don't re-serialize just to clobber whitespace."""
        import time

        _, creds_file = config_dir
        creds_file.write_text(json.dumps({"bluesky": {"handle": "h", "app_password": "p"}}))
        original_mtime = creds_file.stat().st_mtime_ns
        time.sleep(0.01)

        with patch("builtins.input", return_value="n"):
            _auth(_ns(bluesky=True))

        assert creds_file.stat().st_mtime_ns == original_mtime

    def test_atomic_write_leaves_no_tmp_file_on_success(self, config_dir):
        _, creds_file = config_dir
        tmp_path = creds_file.with_suffix(creds_file.suffix + ".tmp")

        with patch("getpass.getpass", return_value="comfy_abc"):
            _auth(_ns(comfy_cloud=True))

        assert creds_file.exists()
        assert not tmp_path.exists()

    def test_atomic_write_preserves_original_when_replace_fails(self, config_dir, monkeypatch):
        _, creds_file = config_dir
        original = {"bluesky": {"handle": "original", "app_password": "original"}}
        creds_file.write_text(json.dumps(original))
        original_bytes = creds_file.read_bytes()
        tmp_path = creds_file.with_suffix(creds_file.suffix + ".tmp")

        def boom(*_a, **_kw):
            raise OSError("simulated crash after tmp write")

        monkeypatch.setattr("os.replace", boom)

        with patch("getpass.getpass", return_value="new_key"), pytest.raises(OSError, match="simulated"):
            _auth(_ns(comfy_cloud=True))

        assert creds_file.read_bytes() == original_bytes
        assert not tmp_path.exists()

    def test_eof_on_any_prompt_exits_cleanly(self, config_dir, capsys):
        """Ctrl-D / closed stdin must produce a clean 'no input available' error,
        not a raw EOFError traceback."""
        _, creds_file = config_dir

        with patch("builtins.input", side_effect=EOFError), pytest.raises(SystemExit, match="1"):
            _auth(_ns(bluesky=True))

        err = capsys.readouterr().err
        assert "no input available" in err
        assert not creds_file.exists()


class TestMain:
    def test_no_args_shows_help(self, capsys):
        with patch("sys.argv", ["slop-studio"]), pytest.raises(SystemExit, match="1"):
            main()

    def test_auth_subcommand(self, config_dir):
        _, creds_file = config_dir
        with (
            patch("sys.argv", ["slop-studio", "auth", "--bluesky"]),
            patch("builtins.input", return_value="me.bsky.social"),
            patch("getpass.getpass", return_value="xxxx-yyyy"),
        ):
            main()
        assert creds_file.exists()

    def test_auth_subcommand_comfy_cloud_flag(self, config_dir):
        _, creds_file = config_dir
        with (
            patch("sys.argv", ["slop-studio", "auth", "--comfy-cloud"]),
            patch("getpass.getpass", return_value="comfy_abcdef"),
        ):
            main()
        data = json.loads(creds_file.read_text())
        assert data == {"comfy_cloud": {"api_key": "comfy_abcdef"}}

    def test_init_subcommand(self, tmp_path):
        with patch("sys.argv", ["slop-studio", "init", str(tmp_path)]), pytest.raises(SystemExit, match="0"):
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
        from unittest.mock import patch

        from slop_studio.cli import _resolve_comfyui_cmd

        comfyui_dir = tmp_path / "ComfyUI"
        comfyui_dir.mkdir()
        main_py = comfyui_dir / "main.py"
        main_py.write_text("# ComfyUI")
        with (
            patch("sys.stdin") as mock_stdin,
            patch("slop_studio.init._COMFYUI_SEARCH_PATHS", [comfyui_dir]),
            patch("slop_studio.init._load_config_toml", return_value={}),
        ):
            mock_stdin.isatty.return_value = False
            result = _resolve_comfyui_cmd()
        assert str(main_py) in result
        assert "python" in result

    def test_resolve_comfyui_cmd_not_found(self, tmp_path, monkeypatch):
        from unittest.mock import patch

        from slop_studio.cli import _resolve_comfyui_cmd

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        with (
            patch("sys.stdin") as mock_stdin,
            patch("slop_studio.init._load_config_toml", return_value={}),
            patch("slop_studio.init._COMFYUI_SEARCH_PATHS", []),
        ):
            mock_stdin.isatty.return_value = False
            result = _resolve_comfyui_cmd()
        assert "/path/to/" in result

    def test_copy_flag_attempts_clipboard(self, capsys, monkeypatch):
        from slop_studio.cli import _desktop_config

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
