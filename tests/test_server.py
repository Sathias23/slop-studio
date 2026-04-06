import asyncio
import importlib
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

import slop_studio.config
from slop_studio.server import ComfyUIManager, safe_tool


@pytest.fixture
def default_url():
    """Return the default COMFYUI_URL for use in tests."""
    return slop_studio.config.COMFYUI_URL


def _get_lifespan():
    """Import lifespan fresh after config reload."""
    import slop_studio.server

    importlib.reload(slop_studio.server)
    return slop_studio.server.lifespan


# --- Lifespan tests (lazy startup — no ComfyUI connectivity check at boot) ---


@pytest.mark.anyio
async def test_lifespan_yields_comfyui_manager():
    """Lifespan yields context dict containing a ComfyUIManager instance."""
    lifespan = _get_lifespan()
    async with lifespan(None) as context:
        assert "comfyui_manager" in context
        from slop_studio.server import ComfyUIManager
        assert isinstance(context["comfyui_manager"], ComfyUIManager)


@pytest.mark.anyio
async def test_lifespan_does_not_spawn_comfyui_at_startup(monkeypatch):
    """Lifespan does NOT spawn ComfyUI at boot, even when COMFYUI_START_CMD is set."""
    monkeypatch.setenv("COMFYUI_START_CMD", "echo hello")
    importlib.reload(slop_studio.config)
    lifespan = _get_lifespan()

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        async with lifespan(None) as context:
            assert "comfyui_manager" in context
        mock_exec.assert_not_called()


@pytest.mark.anyio
async def test_lifespan_does_not_check_comfyui_connectivity():
    """Lifespan does NOT make HTTP requests to ComfyUI at startup."""
    lifespan = _get_lifespan()

    with patch("httpx.AsyncClient") as mock_client_cls:
        async with lifespan(None) as context:
            assert "comfyui_manager" in context
        mock_client_cls.assert_not_called()


@pytest.mark.anyio
async def test_lifespan_uses_configured_url(monkeypatch):
    """Manager uses the configured COMFYUI_URL."""
    custom_url = "http://custom-host:9999"
    monkeypatch.setenv("COMFYUI_URL", custom_url)
    importlib.reload(slop_studio.config)
    lifespan = _get_lifespan()
    async with lifespan(None) as context:
        manager = context["comfyui_manager"]
        assert manager._url == custom_url


@pytest.mark.anyio
async def test_lifespan_calls_shutdown_on_exit():
    """Lifespan calls manager.shutdown() when the context exits."""
    lifespan = _get_lifespan()
    async with lifespan(None) as context:
        manager = context["comfyui_manager"]
        manager.shutdown = AsyncMock()

    manager.shutdown.assert_awaited_once()


# --- ComfyUIManager.ensure_ready tests ---


@pytest.mark.anyio
@respx.mock
async def test_ensure_ready_healthy_comfyui_skips_spawn(default_url):
    """When ComfyUI is healthy, ensure_ready returns None and no subprocess is created."""
    respx.get(f"{default_url}/system_stats").mock(
        return_value=httpx.Response(200, json={"system": {}})
    )
    manager = ComfyUIManager(default_url, start_cmd="echo hello", start_timeout=10)

    result = await manager.ensure_ready()
    assert result is None
    assert manager._process is None


@pytest.mark.anyio
@respx.mock
async def test_ensure_ready_spawns_on_first_call(default_url):
    """When ComfyUI is unreachable and start_cmd is set, ensure_ready spawns it."""
    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: health probe — ComfyUI not running yet
            raise httpx.ConnectError("Connection refused")
        # Subsequent calls from _wait_until_ready: healthy
        return httpx.Response(200, json={"system": {}})

    respx.get(f"{default_url}/system_stats").mock(side_effect=side_effect)

    mock_process = MagicMock()
    mock_process.returncode = None
    mock_process.pid = 12345

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process):
        with patch("os.getpgid", return_value=12345):
            manager = ComfyUIManager(default_url, start_cmd="/usr/bin/comfyui", start_timeout=10)
            result = await manager.ensure_ready()

    assert result is None
    assert manager._process is mock_process
    assert manager._managed is True


@pytest.mark.anyio
@respx.mock
async def test_ensure_ready_respawns_after_crash(default_url):
    """When ComfyUI process crashed, ensure_ready cleans up and respawns."""
    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Health probe: ComfyUI crashed
            raise httpx.ConnectError("Connection refused")
        # After respawn: healthy
        return httpx.Response(200, json={"system": {}})

    respx.get(f"{default_url}/system_stats").mock(side_effect=side_effect)

    # Simulate a crashed process (returncode is set)
    dead_process = MagicMock()
    dead_process.returncode = 1

    new_process = MagicMock()
    new_process.returncode = None
    new_process.pid = 99999

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=new_process):
        with patch("os.getpgid", return_value=99999):
            manager = ComfyUIManager(default_url, start_cmd="/usr/bin/comfyui", start_timeout=10)
            manager._process = dead_process

            result = await manager.ensure_ready()

    assert result is None
    assert manager._process is new_process


@pytest.mark.anyio
@respx.mock
async def test_ensure_ready_external_comfyui_no_start_cmd(default_url):
    """AC5: External ComfyUI running, no start_cmd — health check passes, no spawn."""
    respx.get(f"{default_url}/system_stats").mock(
        return_value=httpx.Response(200, json={"system": {}})
    )
    manager = ComfyUIManager(default_url, start_cmd="", start_timeout=10)

    result = await manager.ensure_ready()
    assert result is None
    assert manager._process is None
    assert manager._managed is False


@pytest.mark.anyio
@respx.mock
async def test_ensure_ready_no_start_cmd_returns_error(default_url):
    """When ComfyUI is unreachable and no start_cmd, ensure_ready returns transient error."""
    respx.get(f"{default_url}/system_stats").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )
    manager = ComfyUIManager(default_url, start_cmd="", start_timeout=10)

    result = await manager.ensure_ready()
    assert result is not None
    assert result["status"] == "error"
    assert result["error_type"] == "unreachable"
    assert "not reachable" in result["error"]
    assert "no COMFYUI_START_CMD" in result["error"]
    assert result["retry_suggested"] is True


@pytest.mark.anyio
@respx.mock
async def test_ensure_ready_spawn_timeout_returns_error(default_url):
    """When spawn succeeds but health check times out, returns error and kills process."""
    respx.get(f"{default_url}/system_stats").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )

    mock_process = MagicMock()
    mock_process.returncode = None
    mock_process.pid = 54321
    mock_process.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process):
        with patch("os.getpgid", return_value=54321):
            with patch("os.killpg") as mock_killpg:
                manager = ComfyUIManager(default_url, start_cmd="/usr/bin/comfyui", start_timeout=0.1)
                result = await manager.ensure_ready()

    assert result is not None
    assert result["status"] == "error"
    assert result["error_type"] == "unreachable"
    assert "did not become ready" in result["error"]
    assert manager._process is None
    # Verify the process was killed
    mock_killpg.assert_called()


@pytest.mark.anyio
@respx.mock
async def test_queue_prompt_calls_ensure_ready(default_url):
    """Verify ensure_ready is called before job submission in queue_prompt."""
    from unittest.mock import patch as mock_patch

    # Mock the manager
    mock_manager = AsyncMock()
    mock_manager.ensure_ready = AsyncMock(return_value=None)

    # Mock the context
    mock_ctx = MagicMock()
    mock_ctx.lifespan_context = {"comfyui_manager": mock_manager}

    # Mock comfyui.queue_prompt
    with mock_patch("slop_studio.comfyui.queue_prompt", new_callable=AsyncMock, return_value={"prompt_id": "abc123"}):
        # Import and call the tool function directly (unwrapped)
        import slop_studio.server
        importlib.reload(slop_studio.server)

        # Call the inner function (safe_tool wraps it, but we want to test ensure_ready integration)
        result = await slop_studio.server.queue_prompt.__wrapped__(
            template_name="test", inputs={"prompt": "hello"}, ctx=mock_ctx
        )

    mock_manager.ensure_ready.assert_awaited_once()
    assert result == {"prompt_id": "abc123"}


@pytest.mark.anyio
@respx.mock
async def test_queue_prompt_short_circuits_on_ensure_ready_error(default_url):
    """If ensure_ready returns an error, queue_prompt returns it immediately without submitting."""
    error_result = {"status": "error", "error_type": "unreachable", "error": "not ready", "retry_suggested": True}

    mock_manager = AsyncMock()
    mock_manager.ensure_ready = AsyncMock(return_value=error_result)

    mock_ctx = MagicMock()
    mock_ctx.lifespan_context = {"comfyui_manager": mock_manager}

    with patch("slop_studio.comfyui.queue_prompt", new_callable=AsyncMock) as mock_qp:
        import slop_studio.server
        importlib.reload(slop_studio.server)

        result = await slop_studio.server.queue_prompt.__wrapped__(
            template_name="test", inputs={"prompt": "hello"}, ctx=mock_ctx
        )

    assert result == error_result
    mock_qp.assert_not_awaited()


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
