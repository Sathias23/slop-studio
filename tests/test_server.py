import importlib
import logging

import httpx
import pytest
import respx

import slop_studio.config
from slop_studio.server import safe_tool


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


# --- Defensive wrapper (safe_tool) tests ---


@pytest.mark.anyio
async def test_safe_tool_catches_runtime_error():
    """Tool raises unexpected RuntimeError → returns error dict, server doesn't crash."""
    @safe_tool
    async def bad_tool():
        raise RuntimeError("something broke")

    result = await bad_tool()
    assert result["status"] == "error"
    assert result["error_type"] == "internal_error"
    assert "RuntimeError" in result["error"]
    assert "something broke" in result["error"]
    assert result["retry_suggested"] is True


@pytest.mark.anyio
async def test_safe_tool_logs_traceback_to_stderr(caplog):
    """Tool raises TypeError → full traceback logged via logger.exception()."""
    @safe_tool
    async def bad_tool():
        raise TypeError("unexpected type")

    with caplog.at_level(logging.ERROR, logger="slop_studio.server"):
        await bad_tool()

    assert any("Unhandled error in tool 'bad_tool'" in r.message for r in caplog.records)
    assert any(r.exc_info is not None for r in caplog.records)


@pytest.mark.anyio
async def test_safe_tool_error_contains_tool_name_not_traceback():
    """Error response contains tool name and exception class, NOT full traceback."""
    @safe_tool
    async def my_tool():
        raise ValueError("bad value")

    result = await my_tool()
    assert "my_tool" in result["error"]
    assert "ValueError" in result["error"]
    assert "Traceback" not in result["error"]


@pytest.mark.anyio
async def test_safe_tool_no_state_corruption_after_failure():
    """Tool that succeeds after a previous tool failure → operates normally."""
    call_count = 0

    @safe_tool
    async def flaky_tool():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("first call fails")
        return {"status": "ok", "count": call_count}

    # First call fails
    result1 = await flaky_tool()
    assert result1["status"] == "error"

    # Second call succeeds — no state corruption
    result2 = await flaky_tool()
    assert result2["status"] == "ok"
    assert result2["count"] == 2


@pytest.mark.anyio
async def test_safe_tool_passes_through_successful_returns():
    """Existing structured errors (transient/terminal) pass through unchanged."""
    expected = {"status": "error", "error_type": "unreachable", "error": "ComfyUI down", "retry_suggested": True}

    @safe_tool
    async def tool_with_structured_error():
        return expected

    result = await tool_with_structured_error()
    assert result == expected


@pytest.mark.anyio
async def test_safe_tool_passes_through_terminal_error():
    """Terminal error (retry_suggested=False) passes through unchanged."""
    expected = {"status": "error", "error_type": "invalid_inputs", "error": "Bad template name", "retry_suggested": False}

    @safe_tool
    async def tool_with_terminal_error():
        return expected

    result = await tool_with_terminal_error()
    assert result == expected
    assert result["retry_suggested"] is False


@pytest.mark.anyio
async def test_safe_tool_preserves_function_metadata():
    """Decorator preserves __name__ and __doc__ via functools.wraps."""
    @safe_tool
    async def my_documented_tool():
        """This is the docstring."""
        return {"status": "ok"}

    assert my_documented_tool.__name__ == "my_documented_tool"
    assert my_documented_tool.__doc__ == "This is the docstring."
