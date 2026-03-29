import importlib

import pytest

import comfyclaude.config
import comfyclaude.comfyui


@pytest.fixture(autouse=True)
def reload_config():
    yield
    importlib.reload(comfyclaude.config)
    importlib.reload(comfyclaude.comfyui)
