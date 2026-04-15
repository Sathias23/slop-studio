import importlib

import pytest

import slop_studio.backends.local
import slop_studio.comfyui
import slop_studio.config


@pytest.fixture(autouse=True)
def reload_config():
    yield
    importlib.reload(slop_studio.config)
    importlib.reload(slop_studio.backends.local)
    importlib.reload(slop_studio.comfyui)
