import importlib

import httpx
import pytest
import respx

import slop_studio.config


@pytest.fixture
def default_url():
    """Return the default COMFYUI_URL for use in tests."""
    return slop_studio.config.COMFYUI_URL


def _get_lifespan():
    """Import lifespan fresh after config reload."""
    import slop_studio.server

    importlib.reload(slop_studio.server)
    return slop_studio.server.lifespan


@pytest.mark.anyio
@respx.mock
async def test_lifespan_succeeds_when_comfyui_reachable(default_url):
    respx.get(f"{default_url}/system_stats").mock(
        return_value=httpx.Response(200, json={"system": {}})
    )
    lifespan = _get_lifespan()
    async with lifespan(None) as context:
        assert context == {}


@pytest.mark.anyio
@respx.mock
async def test_lifespan_fails_when_comfyui_unreachable(default_url):
    respx.get(f"{default_url}/system_stats").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )
    lifespan = _get_lifespan()
    with pytest.raises(httpx.ConnectError):
        async with lifespan(None):
            pass


@pytest.mark.anyio
@respx.mock
async def test_lifespan_fails_on_non_200_response(default_url):
    respx.get(f"{default_url}/system_stats").mock(
        return_value=httpx.Response(500)
    )
    lifespan = _get_lifespan()
    with pytest.raises(httpx.HTTPStatusError):
        async with lifespan(None):
            pass


@pytest.mark.anyio
@respx.mock
async def test_lifespan_uses_configured_url(monkeypatch):
    custom_url = "http://custom-host:9999"
    monkeypatch.setenv("COMFYUI_URL", custom_url)
    importlib.reload(slop_studio.config)

    respx.get(f"{custom_url}/system_stats").mock(
        return_value=httpx.Response(200, json={"system": {}})
    )
    lifespan = _get_lifespan()
    async with lifespan(None) as context:
        assert context == {}
    assert respx.calls.last.request.url == f"{custom_url}/system_stats"


@pytest.mark.anyio
@respx.mock
async def test_lifespan_uses_30s_timeout(default_url, monkeypatch):
    respx.get(f"{default_url}/system_stats").mock(
        return_value=httpx.Response(200, json={"system": {}})
    )
    captured_timeout = None
    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        nonlocal captured_timeout
        captured_timeout = kwargs.get("timeout")
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)
    lifespan = _get_lifespan()
    async with lifespan(None):
        pass
    assert captured_timeout == 30.0


@pytest.mark.anyio
@respx.mock
async def test_lifespan_timeout_triggers_on_slow_response(default_url):
    """Verify that timeout exceptions propagate correctly."""
    respx.get(f"{default_url}/system_stats").mock(
        side_effect=httpx.ReadTimeout("Read timed out")
    )
    lifespan = _get_lifespan()
    with pytest.raises(httpx.TimeoutException):
        async with lifespan(None):
            pass
