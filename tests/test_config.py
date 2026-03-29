import importlib

import comfyclaude.config as config_module


def test_default_comfyui_url():
    assert config_module.COMFYUI_URL == "http://localhost:8188"


def test_default_templates_dir():
    assert config_module.TEMPLATES_DIR == "./templates"


def test_default_output_dir():
    assert config_module.OUTPUT_DIR == "./output"


def test_env_override_comfyui_url(monkeypatch):
    monkeypatch.setenv("COMFYUI_URL", "http://remote:9999")
    importlib.reload(config_module)
    assert config_module.COMFYUI_URL == "http://remote:9999"


def test_env_override_templates_dir(monkeypatch):
    monkeypatch.setenv("COMFYCLAUDE_TEMPLATES_DIR", "/custom/templates")
    importlib.reload(config_module)
    assert config_module.TEMPLATES_DIR == "/custom/templates"


def test_env_override_output_dir(monkeypatch):
    monkeypatch.setenv("COMFYCLAUDE_OUTPUT_DIR", "/custom/output")
    importlib.reload(config_module)
    assert config_module.OUTPUT_DIR == "/custom/output"
