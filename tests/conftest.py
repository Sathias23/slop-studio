import importlib

import pytest

import slop_studio.comfyui
import slop_studio.config


@pytest.fixture(autouse=True)
def reload_config():
    yield
    importlib.reload(slop_studio.config)
    # slop_studio.comfyui's module body reloads slop_studio.backends.local on
    # reload (see the ``if "slop_studio.backends.local" in sys.modules`` guard
    # in comfyui.py). No need to reload backends.local explicitly here — doing
    # so just reloads it twice per test.
    importlib.reload(slop_studio.comfyui)
