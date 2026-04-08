"""Platform-agnostic process management for ComfyUI lifecycle.

Encapsulates all OS-specific process operations (spawn, kill, health check)
behind a unified API. No other module should call os.killpg, os.kill, or
os.getpgid directly — use these functions instead.
"""

import asyncio
import contextlib
import logging
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

# CREATE_NEW_PROCESS_GROUP is only defined on Windows; provide a fallback
# constant so tests can mock the Windows code path on any platform.
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)


async def spawn_subprocess(*args, **kwargs) -> asyncio.subprocess.Process:
    """Spawn a subprocess with platform-appropriate process group isolation.

    On Unix: uses start_new_session=True to create a new process group.
    On Windows: uses CREATE_NEW_PROCESS_GROUP for subprocess isolation.
    """
    if IS_WINDOWS:
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return await asyncio.create_subprocess_exec(*args, **kwargs)


def kill_process_tree(pid: int) -> None:
    """Force-kill a process and all its children.

    On Unix: sends SIGKILL to the process group.
    On Windows: uses taskkill /T /F /PID for tree kill.
    """
    if IS_WINDOWS:
        result = subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True,
        )
        if result.returncode != 0:
            logger.debug("taskkill /F failed for pid %d: %s", pid, result.stderr)
    else:
        try:
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def graceful_kill(pid: int, timeout: float = 5.0) -> None:
    """Gracefully terminate a process tree: SIGTERM → wait → SIGKILL.

    On Unix: SIGTERM to process group, wait, then SIGKILL if still alive.
    On Windows: taskkill /T (graceful tree kill), wait, then taskkill /T /F (force).
    """
    if IS_WINDOWS:
        # Graceful tree kill
        subprocess.run(
            ["taskkill", "/T", "/PID", str(pid)],
            capture_output=True,
        )
        # Wait for exit
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not is_process_alive(pid):
                return
            time.sleep(0.1)
        # Force kill
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True,
        )
    else:
        try:
            pgid = os.getpgid(pid)
        except (ProcessLookupError, PermissionError):
            return

        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return

        # Wait for exit
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not is_process_alive(pid):
                return
            time.sleep(0.1)

        # Force kill — reuse pgid captured at start to avoid PID reuse hazard
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pgid, signal.SIGKILL)


def is_process_alive(pid: int) -> bool:
    """Check if a process with the given PID is alive.

    On Unix: os.kill(pid, 0) — signal 0 checks existence without killing.
    On Windows: tasklist /FI to filter by PID.
    """
    if IS_WINDOWS:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
        )
        return bool(re.search(rf"(?<!\d){re.escape(str(pid))}(?!\d)", result.stdout))
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # Exists but not owned by us


def get_process_cmdline(pid: int) -> str | None:
    """Read the command line of a process. Returns None if unreadable.

    On Linux: reads /proc/{pid}/cmdline.
    On macOS: uses ps -p {pid} -o command=.
    On Windows: uses wmic process where ProcessId={pid} get CommandLine.
    """
    try:
        if IS_WINDOWS:
            result = subprocess.run(
                ["wmic", "process", "where", f"ProcessId={pid}", "get", "CommandLine"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                # wmic output has a header line, then the command line
                lines = [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
                if len(lines) >= 2:
                    return lines[1]
            return None
        elif sys.platform == "linux":
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
            return raw.decode(errors="replace")
        else:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return result.stdout
        return None
    except Exception:
        return None
