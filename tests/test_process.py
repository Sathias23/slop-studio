"""Tests for slop_studio.process — platform abstraction layer.

All OS calls are mocked. Tests for both Unix and Windows paths run on any platform
by patching IS_WINDOWS.
"""

import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from slop_studio.process import (
    get_process_cmdline,
    graceful_kill,
    is_process_alive,
    kill_process_tree,
    spawn_subprocess,
)

# --- spawn_subprocess tests ---


@pytest.mark.anyio
async def test_spawn_subprocess_unix_uses_start_new_session():
    """On Unix, spawn_subprocess passes start_new_session=True."""
    mock_proc = MagicMock()
    with (
        patch("slop_studio.process.IS_WINDOWS", False),
        patch(
            "slop_studio.process.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc
        ) as mock_exec,
    ):
        result = await spawn_subprocess("echo", "hello", stdout=asyncio.subprocess.DEVNULL)

    assert result is mock_proc
    mock_exec.assert_awaited_once_with(
        "echo",
        "hello",
        stdout=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )


@pytest.mark.anyio
async def test_spawn_subprocess_windows_uses_create_new_process_group():
    """On Windows, spawn_subprocess passes CREATE_NEW_PROCESS_GROUP creationflags."""
    from slop_studio.process import CREATE_NEW_PROCESS_GROUP

    mock_proc = MagicMock()
    with (
        patch("slop_studio.process.IS_WINDOWS", True),
        patch(
            "slop_studio.process.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc
        ) as mock_exec,
    ):
        result = await spawn_subprocess("echo", "hello", stdout=asyncio.subprocess.DEVNULL)

    assert result is mock_proc
    mock_exec.assert_awaited_once_with(
        "echo",
        "hello",
        stdout=asyncio.subprocess.DEVNULL,
        creationflags=CREATE_NEW_PROCESS_GROUP,
    )


# --- kill_process_tree tests ---


def test_kill_process_tree_unix_calls_killpg():
    """On Unix, kill_process_tree sends SIGKILL to the process group."""
    with patch("slop_studio.process.IS_WINDOWS", False):
        with patch("slop_studio.process.os.getpgid", return_value=1234) as mock_getpgid:
            with patch("slop_studio.process.os.killpg") as mock_killpg:
                kill_process_tree(1234)

    mock_getpgid.assert_called_once_with(1234)
    mock_killpg.assert_called_once_with(1234, signal.SIGKILL)


def test_kill_process_tree_unix_handles_process_lookup_error():
    """On Unix, ProcessLookupError is silently ignored."""
    with patch("slop_studio.process.IS_WINDOWS", False):
        with patch("slop_studio.process.os.getpgid", side_effect=ProcessLookupError):
            kill_process_tree(9999)  # Should not raise


def test_kill_process_tree_windows_calls_taskkill():
    """On Windows, kill_process_tree uses taskkill /T /F /PID."""
    with patch("slop_studio.process.IS_WINDOWS", True), patch("slop_studio.process.subprocess.run") as mock_run:
        kill_process_tree(5678)

    mock_run.assert_called_once_with(
        ["taskkill", "/T", "/F", "/PID", "5678"],
        capture_output=True,
    )


# --- graceful_kill tests ---


def test_graceful_kill_unix_sigterm_then_process_dies():
    """On Unix, graceful_kill sends SIGTERM and process dies within timeout."""
    call_order = []

    def mock_getpgid(pid):
        call_order.append("getpgid")
        return 4000

    def mock_killpg(pgid, sig):
        call_order.append(f"killpg_{sig}")

    alive_calls = [0]

    def mock_is_alive(pid):
        alive_calls[0] += 1
        # Process dies after first check
        return alive_calls[0] <= 1

    with patch("slop_studio.process.IS_WINDOWS", False):
        with patch("slop_studio.process.os.getpgid", side_effect=mock_getpgid):
            with patch("slop_studio.process.os.killpg", side_effect=mock_killpg):
                with patch("slop_studio.process.is_process_alive", side_effect=mock_is_alive):
                    with patch("slop_studio.process.time.sleep"):
                        graceful_kill(4000, timeout=5.0)

    assert "getpgid" in call_order
    assert f"killpg_{signal.SIGTERM}" in call_order
    # SIGKILL should NOT be called since process died
    assert f"killpg_{signal.SIGKILL}" not in call_order


def test_graceful_kill_unix_sigterm_then_sigkill():
    """On Unix, if process doesn't die after SIGTERM, SIGKILL is sent."""
    with patch("slop_studio.process.IS_WINDOWS", False), patch("slop_studio.process.os.getpgid", return_value=5000):
        with patch("slop_studio.process.os.killpg") as mock_killpg:
            with patch("slop_studio.process.is_process_alive", return_value=True):
                with patch("slop_studio.process.time.sleep"):
                    with patch("slop_studio.process.time.monotonic", side_effect=[0, 0, 100]):
                        graceful_kill(5000, timeout=0.01)

    # Both SIGTERM and SIGKILL should have been called
    calls = mock_killpg.call_args_list
    sigs = [c.args[1] for c in calls]
    assert signal.SIGTERM in sigs
    assert signal.SIGKILL in sigs


def test_graceful_kill_unix_process_already_dead():
    """On Unix, ProcessLookupError on getpgid means process already dead."""
    with patch("slop_studio.process.IS_WINDOWS", False):
        with patch("slop_studio.process.os.getpgid", side_effect=ProcessLookupError):
            graceful_kill(9999)  # Should return silently


def test_graceful_kill_windows_graceful_then_force():
    """On Windows, graceful_kill uses taskkill /T, then taskkill /T /F if needed."""
    with patch("slop_studio.process.IS_WINDOWS", True), patch("slop_studio.process.subprocess.run") as mock_run:
        with patch("slop_studio.process.is_process_alive", return_value=True):
            with patch("slop_studio.process.time.sleep"):
                with patch("slop_studio.process.time.monotonic", side_effect=[0, 0, 100]):
                    graceful_kill(7000, timeout=0.01)

    calls = mock_run.call_args_list
    # First call: graceful
    assert calls[0] == call(["taskkill", "/T", "/PID", "7000"], capture_output=True)
    # Last call: force
    assert calls[-1] == call(["taskkill", "/T", "/F", "/PID", "7000"], capture_output=True)


def test_graceful_kill_windows_process_dies_gracefully():
    """On Windows, if process dies after graceful taskkill, no force kill needed."""
    with patch("slop_studio.process.IS_WINDOWS", True), patch("slop_studio.process.subprocess.run") as mock_run:
        with patch("slop_studio.process.is_process_alive", return_value=False):
            graceful_kill(7000, timeout=5.0)

    # Only the graceful taskkill should have been called
    assert mock_run.call_count == 1
    mock_run.assert_called_once_with(
        ["taskkill", "/T", "/PID", "7000"],
        capture_output=True,
    )


# --- is_process_alive tests ---


def test_is_process_alive_unix_alive():
    """On Unix, os.kill(pid, 0) succeeds → process is alive."""
    with patch("slop_studio.process.IS_WINDOWS", False), patch("slop_studio.process.os.kill"):
        assert is_process_alive(1234) is True


def test_is_process_alive_unix_dead():
    """On Unix, ProcessLookupError → process is dead."""
    with patch("slop_studio.process.IS_WINDOWS", False):
        with patch("slop_studio.process.os.kill", side_effect=ProcessLookupError):
            assert is_process_alive(1234) is False


def test_is_process_alive_unix_permission_error():
    """On Unix, PermissionError → process exists but not owned by us."""
    with patch("slop_studio.process.IS_WINDOWS", False):
        with patch("slop_studio.process.os.kill", side_effect=PermissionError):
            assert is_process_alive(1234) is True


def test_is_process_alive_windows_alive():
    """On Windows, tasklist output contains PID → process is alive."""
    mock_result = MagicMock()
    mock_result.stdout = "python.exe                    1234 Console                    1     50,000 K\n"

    with patch("slop_studio.process.IS_WINDOWS", True):
        with patch("slop_studio.process.subprocess.run", return_value=mock_result):
            assert is_process_alive(1234) is True


def test_is_process_alive_windows_dead():
    """On Windows, tasklist output does not contain PID → process is dead."""
    mock_result = MagicMock()
    mock_result.stdout = "INFO: No tasks are running which match the specified criteria.\n"

    with patch("slop_studio.process.IS_WINDOWS", True):
        with patch("slop_studio.process.subprocess.run", return_value=mock_result):
            assert is_process_alive(9999) is False


# --- get_process_cmdline tests ---


def test_get_process_cmdline_linux():
    """On Linux, reads /proc/{pid}/cmdline."""
    with patch("slop_studio.process.IS_WINDOWS", False), patch("slop_studio.process.sys.platform", "linux"):
        with patch("slop_studio.process.Path") as mock_path:
            mock_path.return_value.read_bytes.return_value = b"python\x00main.py\x00--comfyui"
            result = get_process_cmdline(1234)

    assert result is not None
    assert "python" in result


def test_get_process_cmdline_macos():
    """On macOS, uses ps -p {pid} -o command=."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "python main.py --comfyui\n"

    with patch("slop_studio.process.IS_WINDOWS", False), patch("slop_studio.process.sys.platform", "darwin"):
        with patch("slop_studio.process.subprocess.run", return_value=mock_result):
            result = get_process_cmdline(1234)

    assert result == "python main.py --comfyui\n"


def test_get_process_cmdline_windows():
    """On Windows, uses wmic to get command line."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "CommandLine\npython main.py --comfyui\n\n"

    with patch("slop_studio.process.IS_WINDOWS", True):
        with patch("slop_studio.process.subprocess.run", return_value=mock_result):
            result = get_process_cmdline(1234)

    assert result == "python main.py --comfyui"


def test_get_process_cmdline_windows_failure():
    """On Windows, wmic failure returns None."""
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""

    with patch("slop_studio.process.IS_WINDOWS", True):
        with patch("slop_studio.process.subprocess.run", return_value=mock_result):
            result = get_process_cmdline(1234)

    assert result is None


def test_get_process_cmdline_exception_returns_none():
    """Any exception during cmdline read returns None."""
    with patch("slop_studio.process.IS_WINDOWS", False), patch("slop_studio.process.sys.platform", "linux"):
        with patch("slop_studio.process.Path", side_effect=OSError("file not found")):
            result = get_process_cmdline(1234)

    assert result is None


# --- Platform detection tests ---


def test_platform_detection_dispatches_correctly():
    """IS_WINDOWS flag correctly dispatches platform-specific code."""
    import sys

    # Verify the constant matches the current platform
    from slop_studio.process import IS_WINDOWS

    assert (sys.platform == "win32") == IS_WINDOWS
