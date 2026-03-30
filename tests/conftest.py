import importlib

import pytest

import slop_studio.config
import slop_studio.comfyui


@pytest.fixture(autouse=True)
def reload_config():
    yield
    importlib.reload(slop_studio.config)
    importlib.reload(slop_studio.comfyui)
