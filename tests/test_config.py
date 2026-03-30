import importlib
import os

import pytest

import slop_studio.config as config_module


def test_default_comfyui_url():
    assert config_module.COMFYUI_URL == "http://localhost:8188"


def test_default_templates_dir_is_absolute():
    assert os.path.isabs(config_module.TEMPLATES_DIR)
    assert config_module.TEMPLATES_DIR.endswith("templates")


def test_default_output_dir():
    assert config_module.OUTPUT_DIR == "./output"


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
    assert config_module.OUTPUT_DIR == "./output"


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
