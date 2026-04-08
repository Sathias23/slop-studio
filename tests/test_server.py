import asyncio
import importlib
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

import slop_studio.config
from slop_studio.server import ComfyUIManager, cleanup_orphan, safe_tool


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
    respx.get(f"{default_url}/system_stats").mock(return_value=httpx.Response(200, json={"system": {}}))
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

    with patch("slop_studio.server.spawn_subprocess", new_callable=AsyncMock, return_value=mock_process):
        with patch("slop_studio.server.is_process_alive", return_value=True):
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

    with patch("slop_studio.server.spawn_subprocess", new_callable=AsyncMock, return_value=new_process):
        with patch("slop_studio.server.is_process_alive", return_value=True):
            manager = ComfyUIManager(default_url, start_cmd="/usr/bin/comfyui", start_timeout=10)
            manager._process = dead_process

            result = await manager.ensure_ready()

    assert result is None
    assert manager._process is new_process


@pytest.mark.anyio
@respx.mock
async def test_ensure_ready_external_comfyui_no_start_cmd(default_url):
    """AC5: External ComfyUI running, no start_cmd — health check passes, no spawn."""
    respx.get(f"{default_url}/system_stats").mock(return_value=httpx.Response(200, json={"system": {}}))
    manager = ComfyUIManager(default_url, start_cmd="", start_timeout=10)

    result = await manager.ensure_ready()
    assert result is None
    assert manager._process is None
    assert manager._managed is False


@pytest.mark.anyio
@respx.mock
async def test_ensure_ready_no_start_cmd_returns_error(default_url):
    """When ComfyUI is unreachable and no start_cmd, ensure_ready returns transient error."""
    respx.get(f"{default_url}/system_stats").mock(side_effect=httpx.ConnectError("Connection refused"))
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
    respx.get(f"{default_url}/system_stats").mock(side_effect=httpx.ConnectError("Connection refused"))

    mock_process = MagicMock()
    mock_process.returncode = None
    mock_process.pid = 54321
    mock_process.wait = AsyncMock()

    with patch("slop_studio.server.spawn_subprocess", new_callable=AsyncMock, return_value=mock_process):
        with patch("slop_studio.server.is_process_alive", return_value=True):
            with patch("slop_studio.server.kill_process_tree") as mock_kill_tree:
                manager = ComfyUIManager(default_url, start_cmd="/usr/bin/comfyui", start_timeout=0.1)
                result = await manager.ensure_ready()

    assert result is not None
    assert result["status"] == "error"
    assert result["error_type"] == "unreachable"
    assert "did not become ready" in result["error"]
    assert manager._process is None
    # Verify the process was killed
    mock_kill_tree.assert_called()


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
    expected = {
        "status": "error",
        "error_type": "invalid_inputs",
        "error": "Bad template name",
        "retry_suggested": False,
    }

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


# --- PID file tracking tests ---


@pytest.mark.anyio
@respx.mock
async def test_spawn_writes_pid_file(default_url, tmp_path):
    """After successful spawn, PID file exists and contains the process PID."""
    pid_file = tmp_path / "comfyui.pid"
    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("Connection refused")
        return httpx.Response(200, json={"system": {}})

    respx.get(f"{default_url}/system_stats").mock(side_effect=side_effect)

    mock_process = MagicMock()
    mock_process.returncode = None
    mock_process.pid = 12345

    with patch("slop_studio.server.spawn_subprocess", new_callable=AsyncMock, return_value=mock_process):
        with patch("slop_studio.server.is_process_alive", return_value=True):
            with patch("slop_studio.server.PID_FILE", pid_file):
                manager = ComfyUIManager(default_url, start_cmd="/usr/bin/comfyui", start_timeout=10)
                result = await manager.ensure_ready()

    assert result is None
    assert pid_file.exists()
    assert pid_file.read_text() == "12345"


@pytest.mark.anyio
async def test_shutdown_removes_pid_file(default_url, tmp_path):
    """After shutdown, PID file is removed."""
    pid_file = tmp_path / "comfyui.pid"
    pid_file.write_text("99999")

    mock_process = MagicMock()
    mock_process.returncode = None
    mock_process.pid = 99999
    mock_process.wait = AsyncMock()

    with patch("slop_studio.server.is_process_alive", return_value=True), patch("slop_studio.server.graceful_kill"):
        with patch("slop_studio.server.PID_FILE", pid_file):
            manager = ComfyUIManager(default_url, start_cmd="", start_timeout=10)
            manager._process = mock_process
            await manager.shutdown()

    assert not pid_file.exists()


@pytest.mark.anyio
async def test_kill_process_removes_pid_file(default_url, tmp_path):
    """After _kill_process(), PID file is removed."""
    pid_file = tmp_path / "comfyui.pid"
    pid_file.write_text("88888")

    mock_process = MagicMock()
    mock_process.returncode = None
    mock_process.pid = 88888
    mock_process.wait = AsyncMock()

    with patch("slop_studio.server.kill_process_tree"), patch("slop_studio.server.PID_FILE", pid_file):
        manager = ComfyUIManager(default_url, start_cmd="", start_timeout=10)
        manager._process = mock_process
        await manager._kill_process()

    assert not pid_file.exists()
    assert manager._process is None
    assert manager._managed is False


@pytest.mark.anyio
async def test_pid_file_write_failure_does_not_crash(default_url, tmp_path, caplog):
    """Mock PID file write to raise OSError — spawn still succeeds (NFR4)."""
    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("Connection refused")
        return httpx.Response(200, json={"system": {}})

    mock_process = MagicMock()
    mock_process.returncode = None
    mock_process.pid = 11111

    with respx.mock:
        respx.get(f"{default_url}/system_stats").mock(side_effect=side_effect)
        with patch("slop_studio.server.spawn_subprocess", new_callable=AsyncMock, return_value=mock_process):
            with patch("slop_studio.server.is_process_alive", return_value=True):
                with patch("slop_studio.server.PID_FILE") as mock_pid:
                    mock_pid.parent.mkdir = MagicMock()
                    mock_pid.write_text = MagicMock(side_effect=OSError("Permission denied"))
                    with caplog.at_level(logging.WARNING, logger="slop_studio.server"):
                        manager = ComfyUIManager(default_url, start_cmd="/usr/bin/comfyui", start_timeout=10)
                        result = await manager.ensure_ready()

    # Spawn succeeded despite PID file write failure
    assert result is None
    assert manager._process is mock_process
    assert any("Failed to write PID file" in r.message for r in caplog.records)


# --- Orphan cleanup tests ---


@pytest.mark.anyio
async def test_orphan_cleanup_no_pid_file(tmp_path):
    """No PID file exists — no errors."""
    pid_file = tmp_path / "comfyui.pid"
    await cleanup_orphan(pid_file)  # Should not raise


@pytest.mark.anyio
async def test_orphan_cleanup_dead_process(tmp_path):
    """Stale PID file with dead PID — file removed without error."""
    pid_file = tmp_path / "comfyui.pid"
    pid_file.write_text("999999")

    with patch("slop_studio.server.is_process_alive", return_value=False):
        await cleanup_orphan(pid_file)

    assert not pid_file.exists()


@pytest.mark.anyio
async def test_orphan_cleanup_pid_reuse_safety(tmp_path):
    """PID belongs to non-ComfyUI process — NOT killed, PID file removed."""
    pid_file = tmp_path / "comfyui.pid"
    pid_file.write_text("12345")

    with patch("slop_studio.server.is_process_alive", return_value=True):
        with patch("slop_studio.server.get_process_cmdline", return_value="/usr/bin/firefox"):
            await cleanup_orphan(pid_file)

    assert not pid_file.exists()


@pytest.mark.anyio
async def test_orphan_cleanup_kills_comfyui_process(tmp_path):
    """Stale PID file with live ComfyUI process — killed and PID file removed."""
    pid_file = tmp_path / "comfyui.pid"
    pid_file.write_text("12345")

    with patch("slop_studio.server.is_process_alive", return_value=True):
        with patch("slop_studio.server.get_process_cmdline", return_value="python main.py --comfyui-path /opt/ComfyUI"):
            with patch("slop_studio.server.graceful_kill") as mock_graceful:
                await cleanup_orphan(pid_file)

    assert not pid_file.exists()
    mock_graceful.assert_called_once_with(12345, timeout=5.0)


@pytest.mark.anyio
async def test_orphan_cleanup_invalid_pid_file(tmp_path, caplog):
    """PID file contains garbage — warning logged and file removed."""
    pid_file = tmp_path / "comfyui.pid"
    pid_file.write_text("not-a-number")

    with caplog.at_level(logging.WARNING, logger="slop_studio.server"):
        await cleanup_orphan(pid_file)

    assert not pid_file.exists()
    assert any("invalid content" in r.message for r in caplog.records)


@pytest.mark.anyio
async def test_orphan_cleanup_cmdline_check_failure_does_not_kill(tmp_path):
    """If cmdline check returns None, process is NOT killed — safer to leave orphan."""
    pid_file = tmp_path / "comfyui.pid"
    pid_file.write_text("12345")

    with patch("slop_studio.server.is_process_alive", return_value=True):
        with patch("slop_studio.server.get_process_cmdline", return_value=None):
            with patch("slop_studio.server.graceful_kill") as mock_graceful:
                await cleanup_orphan(pid_file)

    assert not pid_file.exists()
    mock_graceful.assert_not_called()


# --- Idle timeout tests ---


@pytest.mark.anyio
@respx.mock
async def test_idle_watcher_shuts_down_after_timeout(default_url):
    """AC1: After idle timeout, ComfyUI is gracefully shut down."""
    manager = ComfyUIManager(default_url, start_cmd="/usr/bin/comfyui", start_timeout=10, idle_timeout=1)

    mock_process = MagicMock()
    mock_process.returncode = None
    mock_process.pid = 55555
    mock_process.wait = AsyncMock()

    manager._process = mock_process
    manager._managed = True
    manager._last_activity = asyncio.get_event_loop().time()

    with patch("slop_studio.server.is_process_alive", return_value=True), patch("slop_studio.server.graceful_kill"):
        # Start the watcher manually with a short sleep interval
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            # Make sleep advance the clock so the timeout is exceeded
            original_time = asyncio.get_event_loop().time

            call_count = 0

            async def fake_sleep(duration):
                nonlocal call_count
                call_count += 1
                # After first sleep, make time appear to have advanced past idle timeout
                manager._last_activity = original_time() - 2  # 2 seconds ago, timeout is 1

            mock_sleep.side_effect = fake_sleep

            manager._idle_task = asyncio.create_task(manager._idle_watcher())
            await manager._idle_task

    # Shutdown should have been called — process should be cleaned up
    assert manager._process is None


@pytest.mark.anyio
async def test_idle_timer_resets_on_activity(default_url):
    """AC4: Activity resets the idle timer — ComfyUI stays running."""
    manager = ComfyUIManager(default_url, start_cmd="/usr/bin/comfyui", start_timeout=10, idle_timeout=2)

    mock_process = MagicMock()
    mock_process.returncode = None
    mock_process.pid = 55555
    mock_process.wait = AsyncMock()

    manager._process = mock_process
    manager._managed = True
    manager._last_activity = asyncio.get_event_loop().time()

    sleep_count = 0

    async def fake_sleep(duration):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count == 1:
            # Simulate activity: reset the timer
            manager._last_activity = asyncio.get_event_loop().time()
        elif sleep_count == 2:
            # Second check: still within timeout, but let's stop the watcher
            manager._process = None  # Causes the watcher to exit the loop

    with patch("asyncio.sleep", new_callable=AsyncMock, side_effect=fake_sleep):
        manager._idle_task = asyncio.create_task(manager._idle_watcher())
        await manager._idle_task

    # The watcher exited because _process was set to None, not because it shut down
    # The fact that we got here without shutdown means the timer reset worked
    assert sleep_count == 2


@pytest.mark.anyio
async def test_idle_timeout_zero_disables_watcher(default_url):
    """AC5: idle_timeout=0 disables the idle watcher — no background task created."""
    manager = ComfyUIManager(default_url, start_cmd="/usr/bin/comfyui", start_timeout=10, idle_timeout=0)
    manager._managed = True

    manager._start_idle_watcher()

    assert manager._idle_task is None


@pytest.mark.anyio
async def test_idle_watcher_only_for_managed_process(default_url):
    """Idle watcher is NOT started for external (unmanaged) ComfyUI."""
    manager = ComfyUIManager(default_url, start_cmd="", start_timeout=10, idle_timeout=60)
    manager._managed = False

    manager._start_idle_watcher()

    assert manager._idle_task is None


@pytest.mark.anyio
async def test_idle_watcher_cancelled_on_shutdown(default_url):
    """AC6: Idle watcher is cancelled cleanly on shutdown."""
    manager = ComfyUIManager(default_url, start_cmd="/usr/bin/comfyui", start_timeout=10, idle_timeout=900)

    mock_process = MagicMock()
    mock_process.returncode = None
    mock_process.pid = 55555
    mock_process.wait = AsyncMock()

    manager._process = mock_process
    manager._managed = True
    manager._last_activity = asyncio.get_event_loop().time()

    # Start a real idle watcher that will block on sleep
    manager._idle_task = asyncio.create_task(manager._idle_watcher())

    # Give the task a moment to start
    await asyncio.sleep(0)

    with patch("slop_studio.server.is_process_alive", return_value=True), patch("slop_studio.server.graceful_kill"):
        await manager.shutdown()

    assert manager._idle_task is None
    assert manager._process is None


@pytest.mark.anyio
@respx.mock
async def test_respawn_after_idle_shutdown(default_url):
    """AC3: After idle shutdown, next ensure_ready re-spawns ComfyUI and restarts watcher."""
    manager = ComfyUIManager(default_url, start_cmd="/usr/bin/comfyui", start_timeout=10, idle_timeout=60)

    # Simulate post-idle-shutdown state: no process, not managed
    manager._process = None
    manager._managed = False
    manager._idle_task = None

    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("Connection refused")
        return httpx.Response(200, json={"system": {}})

    respx.get(f"{default_url}/system_stats").mock(side_effect=side_effect)

    mock_process = MagicMock()
    mock_process.returncode = None
    mock_process.pid = 77777

    with patch("slop_studio.server.spawn_subprocess", new_callable=AsyncMock, return_value=mock_process):
        with patch("slop_studio.server.is_process_alive", return_value=True):
            result = await manager.ensure_ready()

    assert result is None
    assert manager._process is mock_process
    assert manager._managed is True
    # Idle watcher should have been started for the new process
    assert manager._idle_task is not None
    assert not manager._idle_task.done()

    # Clean up the watcher task
    try:
        manager._idle_task.cancel()
        await manager._idle_task
    except asyncio.CancelledError:
        pass


def test_negative_idle_timeout_rejected(monkeypatch):
    """AC: Negative COMFYUI_IDLE_TIMEOUT raises ValueError."""
    monkeypatch.setenv("COMFYUI_IDLE_TIMEOUT", "-1")
    with pytest.raises(ValueError, match="must be >= 0"):
        importlib.reload(slop_studio.config)


@pytest.mark.anyio
async def test_idle_watcher_double_check_after_lock(default_url):
    """Race condition prevention: activity between idle check and lock acquisition prevents shutdown."""
    manager = ComfyUIManager(default_url, start_cmd="/usr/bin/comfyui", start_timeout=10, idle_timeout=1)

    mock_process = MagicMock()
    mock_process.returncode = None
    mock_process.pid = 55555

    manager._process = mock_process
    manager._managed = True

    original_time = asyncio.get_event_loop().time
    sleep_count = 0

    shutdown_called = False

    async def mock_shutdown():
        nonlocal shutdown_called
        shutdown_called = True

    manager.shutdown = mock_shutdown

    async def fake_sleep(duration):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count == 1:
            # Set activity to the past so first check passes
            manager._last_activity = original_time() - 2
        elif sleep_count >= 2:
            # Stop the loop
            manager._process = None

    # Patch lock to simulate activity during lock acquisition
    original_lock = manager._lock

    class FakeContextManager:
        async def __aenter__(self_inner):
            await original_lock.acquire()
            # Simulate activity occurring while waiting for lock
            manager._last_activity = original_time()
            return self_inner

        async def __aexit__(self_inner, *args):
            original_lock.release()

    manager._lock = FakeContextManager()

    with patch("asyncio.sleep", new_callable=AsyncMock, side_effect=fake_sleep):
        manager._idle_task = asyncio.create_task(manager._idle_watcher())
        await manager._idle_task

    # Shutdown should NOT have been called because activity happened after lock acquisition
    assert not shutdown_called


# --- open_image tool tests ---


@pytest.mark.anyio
async def test_open_image_file_not_found(tmp_path):
    from slop_studio.server import open_image

    with patch("slop_studio.config.OUTPUT_DIR", str(tmp_path)):
        result = await open_image(str(tmp_path / "nonexistent.png"))
    assert result["status"] == "error"
    assert "not found" in result["error"].lower()


@pytest.mark.anyio
async def test_open_image_bad_extension(tmp_path):
    from slop_studio.server import open_image

    bad = tmp_path / "script.sh"
    bad.write_bytes(b"#!/bin/sh")
    with patch("slop_studio.config.OUTPUT_DIR", str(tmp_path)):
        result = await open_image(str(bad))
    assert result["status"] == "error"
    assert "unsupported file type" in result["error"].lower()


@pytest.mark.anyio
async def test_open_image_outside_output_dir(tmp_path):
    from slop_studio.server import open_image

    img = tmp_path / "evil.png"
    img.write_bytes(b"fake image")
    with patch("slop_studio.config.OUTPUT_DIR", str(tmp_path / "output")):
        result = await open_image(str(img))
    assert result["status"] == "error"
    assert "output directory" in result["error"].lower()


@pytest.mark.anyio
async def test_open_image_success(tmp_path):
    from slop_studio.server import open_image

    img = tmp_path / "test.png"
    img.write_bytes(b"fake image")
    mock_proc = AsyncMock()
    with (
        patch("slop_studio.config.OUTPUT_DIR", str(tmp_path)),
        patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec,
    ):
        result = await open_image(str(img))
    assert result["status"] == "success"
    assert result["file_path"] == str(img)
    mock_exec.assert_called_once()


@pytest.mark.anyio
async def test_open_image_popen_failure(tmp_path):
    from slop_studio.server import open_image

    img = tmp_path / "test.png"
    img.write_bytes(b"fake image")
    with (
        patch("slop_studio.config.OUTPUT_DIR", str(tmp_path)),
        patch("asyncio.create_subprocess_exec", side_effect=OSError("no viewer")),
    ):
        result = await open_image(str(img))
    assert result["status"] == "error"
    assert "no viewer" in result["error"]
