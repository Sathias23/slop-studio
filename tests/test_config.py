import importlib
import json
import logging
import os
from pathlib import Path

import pytest

import slop_studio.config as config_module


def test_default_comfyui_url():
    assert config_module.COMFYUI_URL == "http://localhost:8188"


def test_default_templates_dir_is_absolute():
    assert os.path.isabs(config_module.TEMPLATES_DIR)
    assert config_module.TEMPLATES_DIR.endswith("templates")


def test_default_output_dir():
    assert os.path.isabs(config_module.OUTPUT_DIR)
    assert config_module.OUTPUT_DIR.endswith("slop-studio/output")


def test_default_output_dir_starts_with_home():
    assert config_module.OUTPUT_DIR.startswith(str(Path.home()))


def test_env_override_comfyui_url(monkeypatch):
    monkeypatch.setenv("COMFYUI_URL", "http://remote:9999")
    importlib.reload(config_module)
    assert config_module.COMFYUI_URL == "http://remote:9999"


def test_env_override_templates_dir(monkeypatch):
    monkeypatch.setenv("SLOP_STUDIO_TEMPLATES_DIR", "/custom/templates")
    importlib.reload(config_module)
    assert config_module.TEMPLATES_DIR == "/custom/templates"


def test_env_override_output_dir(monkeypatch):
    monkeypatch.setenv("SLOP_STUDIO_OUTPUT_DIR", "/custom/output")
    importlib.reload(config_module)
    assert config_module.OUTPUT_DIR == "/custom/output"


def test_empty_comfyui_url_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("COMFYUI_URL", "")
    importlib.reload(config_module)
    assert config_module.COMFYUI_URL == "http://localhost:8188"


def test_empty_templates_dir_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("SLOP_STUDIO_TEMPLATES_DIR", "")
    importlib.reload(config_module)
    assert os.path.isabs(config_module.TEMPLATES_DIR)
    assert config_module.TEMPLATES_DIR.endswith("templates")


def test_empty_output_dir_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("SLOP_STUDIO_OUTPUT_DIR", "")
    importlib.reload(config_module)
    assert os.path.isabs(config_module.OUTPUT_DIR)
    assert config_module.OUTPUT_DIR.endswith("slop-studio/output")


def test_trailing_slash_stripped(monkeypatch):
    monkeypatch.setenv("COMFYUI_URL", "http://host:8188/")
    importlib.reload(config_module)
    assert config_module.COMFYUI_URL == "http://host:8188"


def test_multiple_trailing_slashes_stripped(monkeypatch):
    monkeypatch.setenv("COMFYUI_URL", "http://host:8188///")
    importlib.reload(config_module)
    assert config_module.COMFYUI_URL == "http://host:8188"


def test_invalid_url_scheme_raises(monkeypatch):
    monkeypatch.setenv("COMFYUI_URL", "ftp://bad:8188")
    with pytest.raises(ValueError, match="must start with http"):
        importlib.reload(config_module)


def test_plain_string_url_raises(monkeypatch):
    monkeypatch.setenv("COMFYUI_URL", "not-a-url")
    with pytest.raises(ValueError, match="must start with http"):
        importlib.reload(config_module)


def test_project_dir_overrides_default_output_dir(monkeypatch):
    """Simulate --project-dir by setting env var before config reload (same as cli.py)."""
    monkeypatch.setenv("SLOP_STUDIO_OUTPUT_DIR", "/my-art/output")
    importlib.reload(config_module)
    assert config_module.OUTPUT_DIR == "/my-art/output"


# --- config.toml loading tests (Story 1.2, Task 3) ---


def _setup_config_toml(monkeypatch, tmp_path, content=None):
    """Helper: set up a fake home dir so CONFIG_FILE resolves to tmp_path."""
    config_dir = tmp_path / ".config" / "slop-studio"
    config_dir.mkdir(parents=True, exist_ok=True)
    if content is not None:
        (config_dir / "config.toml").write_text(content)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))


def test_config_toml_values_used(monkeypatch, tmp_path):
    """AC #1: config.toml with output_dir and templates_dir → values used when no env vars set."""
    _setup_config_toml(monkeypatch, tmp_path, 'output_dir = "/my/images"\ntemplates_dir = "/my/templates"\n')
    monkeypatch.delenv("SLOP_STUDIO_OUTPUT_DIR", raising=False)
    monkeypatch.delenv("SLOP_STUDIO_TEMPLATES_DIR", raising=False)
    importlib.reload(config_module)
    assert config_module.OUTPUT_DIR == "/my/images"
    assert config_module.TEMPLATES_DIR == "/my/templates"


def test_config_toml_missing_falls_through(monkeypatch, tmp_path):
    """AC #3: config.toml missing → falls through to defaults, no error."""
    _setup_config_toml(monkeypatch, tmp_path)  # no content = no config.toml file
    (tmp_path / ".config" / "slop-studio" / "config.toml").unlink(missing_ok=True)
    monkeypatch.delenv("SLOP_STUDIO_OUTPUT_DIR", raising=False)
    monkeypatch.delenv("SLOP_STUDIO_TEMPLATES_DIR", raising=False)
    importlib.reload(config_module)
    assert str(tmp_path / "slop-studio" / "output") == config_module.OUTPUT_DIR


def test_config_toml_invalid_warns_and_falls_through(monkeypatch, tmp_path, caplog):
    """AC #4: invalid TOML → warning logged, falls through to defaults."""
    _setup_config_toml(monkeypatch, tmp_path, "this is not valid [[ toml")
    monkeypatch.delenv("SLOP_STUDIO_OUTPUT_DIR", raising=False)
    monkeypatch.delenv("SLOP_STUDIO_TEMPLATES_DIR", raising=False)
    import logging

    with caplog.at_level(logging.WARNING, logger="slop_studio.config"):
        importlib.reload(config_module)
    assert str(tmp_path / "slop-studio" / "output") == config_module.OUTPUT_DIR
    assert any("Invalid TOML" in msg for msg in caplog.messages)


def test_config_toml_partial_keys(monkeypatch, tmp_path):
    """Partial config.toml: only output_dir → templates_dir uses default."""
    _setup_config_toml(monkeypatch, tmp_path, 'output_dir = "/custom/output"\n')
    monkeypatch.delenv("SLOP_STUDIO_OUTPUT_DIR", raising=False)
    monkeypatch.delenv("SLOP_STUDIO_TEMPLATES_DIR", raising=False)
    importlib.reload(config_module)
    assert config_module.OUTPUT_DIR == "/custom/output"
    assert config_module.TEMPLATES_DIR.endswith("templates")
    assert config_module.TEMPLATES_DIR != "/custom/output"


# --- Priority hierarchy tests (Story 1.2, Task 4) ---


def test_env_var_wins_over_config_toml(monkeypatch, tmp_path):
    """AC #2: env var set + config.toml exists → env var wins."""
    _setup_config_toml(monkeypatch, tmp_path, 'output_dir = "/toml/output"\ntemplates_dir = "/toml/templates"\n')
    monkeypatch.setenv("SLOP_STUDIO_OUTPUT_DIR", "/env/output")
    monkeypatch.setenv("SLOP_STUDIO_TEMPLATES_DIR", "/env/templates")
    importlib.reload(config_module)
    assert config_module.OUTPUT_DIR == "/env/output"
    assert config_module.TEMPLATES_DIR == "/env/templates"


def test_project_dir_wins_over_config_toml(monkeypatch, tmp_path):
    """AC #5: --project-dir (via env var) + config.toml exists → --project-dir wins."""
    _setup_config_toml(monkeypatch, tmp_path, 'output_dir = "/toml/output"\n')
    monkeypatch.setenv("SLOP_STUDIO_OUTPUT_DIR", "/project-dir/output")
    importlib.reload(config_module)
    assert config_module.OUTPUT_DIR == "/project-dir/output"


def test_no_env_no_toml_uses_absolute_default(monkeypatch, tmp_path):
    """AC #5: no env var, no config.toml → absolute default used."""
    _setup_config_toml(monkeypatch, tmp_path)  # no config.toml
    (tmp_path / ".config" / "slop-studio" / "config.toml").unlink(missing_ok=True)
    monkeypatch.delenv("SLOP_STUDIO_OUTPUT_DIR", raising=False)
    monkeypatch.delenv("SLOP_STUDIO_TEMPLATES_DIR", raising=False)
    importlib.reload(config_module)
    assert str(tmp_path / "slop-studio" / "output") == config_module.OUTPUT_DIR


def test_config_toml_permission_error_warns_and_falls_through(monkeypatch, tmp_path, caplog):
    """OSError (e.g. permission denied) on config.toml → warning logged, falls through."""
    _setup_config_toml(monkeypatch, tmp_path, 'output_dir = "/my/images"\n')
    config_file = tmp_path / ".config" / "slop-studio" / "config.toml"
    config_file.chmod(0o000)
    monkeypatch.delenv("SLOP_STUDIO_OUTPUT_DIR", raising=False)
    import logging

    with caplog.at_level(logging.WARNING, logger="slop_studio.config"):
        importlib.reload(config_module)
    assert str(tmp_path / "slop-studio" / "output") == config_module.OUTPUT_DIR
    assert any("Cannot read" in msg for msg in caplog.messages)
    config_file.chmod(0o644)  # restore for cleanup


def test_config_toml_non_string_value_warns(monkeypatch, tmp_path, caplog):
    """Non-string TOML value (e.g. integer) → warning logged, falls through to default."""
    _setup_config_toml(monkeypatch, tmp_path, "output_dir = 42\n")
    monkeypatch.delenv("SLOP_STUDIO_OUTPUT_DIR", raising=False)
    import logging

    with caplog.at_level(logging.WARNING, logger="slop_studio.config"):
        importlib.reload(config_module)
    assert str(tmp_path / "slop-studio" / "output") == config_module.OUTPUT_DIR
    assert any("must be a string" in msg for msg in caplog.messages)


def test_config_toml_whitespace_only_value_warns(monkeypatch, tmp_path, caplog):
    """Whitespace-only TOML value → warning logged, falls through to default."""
    _setup_config_toml(monkeypatch, tmp_path, 'output_dir = "   "\n')
    monkeypatch.delenv("SLOP_STUDIO_OUTPUT_DIR", raising=False)
    import logging

    with caplog.at_level(logging.WARNING, logger="slop_studio.config"):
        importlib.reload(config_module)
    assert str(tmp_path / "slop-studio" / "output") == config_module.OUTPUT_DIR
    assert any("is blank" in msg for msg in caplog.messages)


# --- COMFYUI_START_CMD config.toml integration ---


def test_comfyui_start_cmd_from_config_toml(monkeypatch, tmp_path):
    """comfyui_start_cmd in config.toml is used when env var is unset."""
    _setup_config_toml(
        monkeypatch,
        tmp_path,
        'comfyui_start_cmd = "python3 /home/user/ComfyUI/main.py"\n',
    )
    monkeypatch.delenv("COMFYUI_START_CMD", raising=False)
    importlib.reload(config_module)
    assert config_module.COMFYUI_START_CMD == "python3 /home/user/ComfyUI/main.py"


def test_comfyui_start_cmd_env_wins_over_toml(monkeypatch, tmp_path):
    """Env var COMFYUI_START_CMD takes priority over config.toml."""
    _setup_config_toml(
        monkeypatch,
        tmp_path,
        'comfyui_start_cmd = "from-toml"\n',
    )
    monkeypatch.setenv("COMFYUI_START_CMD", "from-env")
    importlib.reload(config_module)
    assert config_module.COMFYUI_START_CMD == "from-env"


def test_comfyui_start_cmd_defaults_to_empty(monkeypatch, tmp_path):
    """No env var, no config.toml → empty string (auto-start disabled)."""
    _setup_config_toml(monkeypatch, tmp_path)
    (tmp_path / ".config" / "slop-studio" / "config.toml").unlink(missing_ok=True)
    monkeypatch.delenv("COMFYUI_START_CMD", raising=False)
    importlib.reload(config_module)
    assert config_module.COMFYUI_START_CMD == ""


# ---------------------------------------------------------------------------
# Story 6.5 — Comfy Cloud config surfaces.
# ---------------------------------------------------------------------------


def _setup_credentials_json(monkeypatch, tmp_path, data):
    """Write credentials.json in a tmp-path-rooted fake home dir.

    Idempotent on ``Path.home`` — callers can invoke _setup_config_toml
    first; this helper re-applies the same patch safely.
    """
    config_dir = tmp_path / ".config" / "slop-studio"
    config_dir.mkdir(parents=True, exist_ok=True)
    if data is not None:
        (config_dir / "credentials.json").write_text(json.dumps(data))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))


def _reset_cloud_env(monkeypatch):
    """Clear all Comfy Cloud env vars so tests start from a known state."""
    monkeypatch.delenv("COMFY_CLOUD_API_KEY", raising=False)
    monkeypatch.delenv("COMFY_CLOUD_URL", raising=False)
    monkeypatch.delenv("SLOP_STUDIO_DEFAULT_BACKEND", raising=False)


def test_default_comfy_cloud_url(monkeypatch, tmp_path):
    _setup_config_toml(monkeypatch, tmp_path)
    (tmp_path / ".config" / "slop-studio" / "config.toml").unlink(missing_ok=True)
    _reset_cloud_env(monkeypatch)
    importlib.reload(config_module)
    assert config_module.COMFY_CLOUD_URL == "https://cloud.comfy.org"


def test_env_override_comfy_cloud_url(monkeypatch):
    monkeypatch.setenv("COMFY_CLOUD_URL", "https://staging.example.com/")
    importlib.reload(config_module)
    assert config_module.COMFY_CLOUD_URL == "https://staging.example.com"


def test_config_toml_comfy_cloud_url(monkeypatch, tmp_path):
    _setup_config_toml(monkeypatch, tmp_path, 'comfy_cloud_url = "https://toml.example.com"\n')
    _reset_cloud_env(monkeypatch)
    importlib.reload(config_module)
    assert config_module.COMFY_CLOUD_URL == "https://toml.example.com"


def test_default_default_backend(monkeypatch, tmp_path):
    _setup_config_toml(monkeypatch, tmp_path)
    (tmp_path / ".config" / "slop-studio" / "config.toml").unlink(missing_ok=True)
    _reset_cloud_env(monkeypatch)
    importlib.reload(config_module)
    assert config_module.DEFAULT_BACKEND == "local"


def test_env_override_default_backend(monkeypatch):
    monkeypatch.setenv("SLOP_STUDIO_DEFAULT_BACKEND", "cloud")
    importlib.reload(config_module)
    assert config_module.DEFAULT_BACKEND == "cloud"


def test_invalid_default_backend_falls_back_with_warning(monkeypatch, caplog):
    monkeypatch.setenv("SLOP_STUDIO_DEFAULT_BACKEND", "remote")
    with caplog.at_level(logging.WARNING, logger="slop_studio.config"):
        importlib.reload(config_module)
    assert config_module.DEFAULT_BACKEND == "local"
    assert any("must be 'local' or 'cloud'" in msg for msg in caplog.messages)


def test_get_comfy_cloud_api_key_credentials_json(monkeypatch, tmp_path):
    _setup_credentials_json(monkeypatch, tmp_path, {"comfy_cloud": {"api_key": "from-file"}})
    monkeypatch.delenv("COMFY_CLOUD_API_KEY", raising=False)
    importlib.reload(config_module)
    assert config_module.get_comfy_cloud_api_key() == "from-file"


def test_get_comfy_cloud_api_key_both_env_wins(monkeypatch, tmp_path):
    _setup_credentials_json(monkeypatch, tmp_path, {"comfy_cloud": {"api_key": "from-file"}})
    monkeypatch.setenv("COMFY_CLOUD_API_KEY", "from-env")
    importlib.reload(config_module)
    assert config_module.get_comfy_cloud_api_key() == "from-env"


def test_get_comfy_cloud_api_key_missing_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.delenv("COMFY_CLOUD_API_KEY", raising=False)
    importlib.reload(config_module)
    assert config_module.get_comfy_cloud_api_key() == ""


def test_get_comfy_cloud_api_key_invalid_json(monkeypatch, tmp_path):
    config_dir = tmp_path / ".config" / "slop-studio"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "credentials.json").write_text("this is not json {{{{")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.delenv("COMFY_CLOUD_API_KEY", raising=False)
    importlib.reload(config_module)
    assert config_module.get_comfy_cloud_api_key() == ""


def test_get_comfy_cloud_api_key_missing_comfy_cloud_key(monkeypatch, tmp_path):
    _setup_credentials_json(monkeypatch, tmp_path, {"bluesky": {"handle": "x", "app_password": "y"}})
    monkeypatch.delenv("COMFY_CLOUD_API_KEY", raising=False)
    importlib.reload(config_module)
    assert config_module.get_comfy_cloud_api_key() == ""


def test_get_comfy_cloud_api_key_missing_api_key_field(monkeypatch, tmp_path):
    _setup_credentials_json(monkeypatch, tmp_path, {"comfy_cloud": {}})
    monkeypatch.delenv("COMFY_CLOUD_API_KEY", raising=False)
    importlib.reload(config_module)
    assert config_module.get_comfy_cloud_api_key() == ""


def test_get_comfy_cloud_api_key_non_string_value(monkeypatch, tmp_path):
    _setup_credentials_json(monkeypatch, tmp_path, {"comfy_cloud": {"api_key": 42}})
    monkeypatch.delenv("COMFY_CLOUD_API_KEY", raising=False)
    importlib.reload(config_module)
    assert config_module.get_comfy_cloud_api_key() == ""


def test_get_comfy_cloud_api_key_whitespace_value(monkeypatch, tmp_path):
    _setup_credentials_json(monkeypatch, tmp_path, {"comfy_cloud": {"api_key": "   "}})
    monkeypatch.delenv("COMFY_CLOUD_API_KEY", raising=False)
    importlib.reload(config_module)
    assert config_module.get_comfy_cloud_api_key() == ""


def test_get_comfy_cloud_api_key_unresolved_placeholder_env(monkeypatch, tmp_path):
    _setup_credentials_json(monkeypatch, tmp_path, {"comfy_cloud": {"api_key": "from-file"}})
    monkeypatch.setenv("COMFY_CLOUD_API_KEY", "${DEFINITELY_UNDEFINED_XYZ}")
    monkeypatch.delenv("DEFINITELY_UNDEFINED_XYZ", raising=False)
    importlib.reload(config_module)
    assert config_module.get_comfy_cloud_api_key() == "from-file"


def test_get_comfy_cloud_api_key_coexists_with_bluesky(monkeypatch, tmp_path):
    _setup_credentials_json(
        monkeypatch,
        tmp_path,
        {
            "bluesky": {"handle": "alice.bsky.social", "app_password": "pw"},
            "comfy_cloud": {"api_key": "cloud-key"},
        },
    )
    monkeypatch.delenv("COMFY_CLOUD_API_KEY", raising=False)
    monkeypatch.delenv("BSKY_HANDLE", raising=False)
    monkeypatch.delenv("BSKY_APP_PASSWORD", raising=False)
    importlib.reload(config_module)
    assert config_module.get_comfy_cloud_api_key() == "cloud-key"
    assert config_module.get_bsky_credentials() == ("alice.bsky.social", "pw")


def test_no_raw_api_key_in_logs_or_errors(monkeypatch, tmp_path, caplog):
    """NFR-C3 canary: raw key MUST NOT appear in any log or error message."""
    unique_key = "comfyui-DONOTLEAK-abcdef0123456789"
    monkeypatch.setenv("COMFY_CLOUD_API_KEY", unique_key)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    importlib.reload(config_module)
    with caplog.at_level(logging.DEBUG):
        returned = config_module.get_comfy_cloud_api_key()
    assert returned == unique_key
    for record in caplog.records:
        assert unique_key not in record.getMessage()
    assert unique_key not in caplog.text


def test_get_comfy_cloud_api_key_strips_whitespace(monkeypatch, tmp_path):
    """Env-var key with surrounding whitespace is stripped."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setenv("COMFY_CLOUD_API_KEY", "  real-key  ")
    importlib.reload(config_module)
    assert config_module.get_comfy_cloud_api_key() == "real-key"
