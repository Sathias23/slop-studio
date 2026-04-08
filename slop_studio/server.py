import asyncio
import atexit
import functools
import logging
import shlex
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import httpx
from fastmcp import FastMCP
from fastmcp.server.context import Context

from slop_studio.config import (
    COMFYUI_IDLE_TIMEOUT,
    COMFYUI_START_CMD,
    COMFYUI_START_TIMEOUT,
    COMFYUI_URL,
    PID_FILE,
)
from slop_studio.errors import transient_error
from slop_studio.process import (
    get_process_cmdline,
    graceful_kill,
    is_process_alive,
    kill_process_tree,
    spawn_subprocess,
)

logger = logging.getLogger(__name__)


def safe_tool(func):
    """Wrap MCP tool handler with defensive error catching.

    Catches all exceptions except BaseException subclasses (KeyboardInterrupt,
    SystemExit). Logs full traceback to stderr, returns human-readable error.
    """

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as exc:
            logger.exception("Unhandled error in tool '%s'", func.__name__)
            return transient_error(
                "internal_error",
                f"An internal error occurred in {func.__name__}: {type(exc).__name__}: {exc}",
            )

    return wrapper


class ComfyUIManager:
    """Manages the ComfyUI subprocess lifecycle with lazy startup.

    ComfyUI is not started at server boot. Instead, ensure_ready() is called
    before each queue_prompt to start ComfyUI on demand (if configured) and
    verify it is healthy.
    """

    def __init__(self, url: str, start_cmd: str, start_timeout: float, idle_timeout: int = 900):
        self._url = url
        self._start_cmd = start_cmd
        self._start_timeout = start_timeout
        self._idle_timeout = idle_timeout
        self._process: asyncio.subprocess.Process | None = None
        self._managed: bool = False
        self._atexit_handler = None
        self._lock = asyncio.Lock()
        self._last_activity: float = 0.0
        self._idle_task: asyncio.Task | None = None

    def _write_pid_file(self) -> None:
        """Write the managed process PID to the PID file. Non-fatal on failure."""
        if not self._process:
            return
        try:
            PID_FILE.parent.mkdir(parents=True, exist_ok=True)
            PID_FILE.write_text(str(self._process.pid))
        except OSError as exc:
            logger.warning("Failed to write PID file %s: %s", PID_FILE, exc)

    def _remove_pid_file(self) -> None:
        """Remove the PID file if it exists. Non-fatal on failure."""
        try:
            PID_FILE.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed to remove PID file %s: %s", PID_FILE, exc)

    async def _cancel_idle_watcher(self) -> None:
        """Cancel the idle watcher task if running."""
        if self._idle_task is not None and not self._idle_task.done():
            self._idle_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._idle_task
        self._idle_task = None

    async def _idle_watcher(self) -> None:
        """Background task that shuts down ComfyUI after idle timeout."""
        try:
            while True:
                await asyncio.sleep(30)
                if not self._process or not self._managed:
                    break
                elapsed = asyncio.get_running_loop().time() - self._last_activity
                if elapsed < self._idle_timeout:
                    continue
                async with self._lock:
                    # Re-check after acquiring lock — activity may have occurred
                    elapsed = asyncio.get_running_loop().time() - self._last_activity
                    if elapsed < self._idle_timeout:
                        continue
                    if not self._process or not self._managed:
                        break
                    logger.info("ComfyUI idle for %ds — shutting down", int(elapsed))
                    self._idle_task = None
                # Release the lock before shutdown — shutdown waits for process exit
                # which can take up to 10s and would block all ensure_ready() calls.
                await self.shutdown()
                break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Idle watcher encountered an error")

    def _start_idle_watcher(self) -> None:
        """Start the idle watcher task if conditions are met."""
        if self._idle_timeout <= 0 or not self._managed:
            return
        if self._idle_task is not None and not self._idle_task.done():
            return
        self._idle_task = asyncio.create_task(self._idle_watcher())

    async def _wait_until_ready(self) -> bool:
        """Poll ComfyUI until it responds, using exponential backoff."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._start_timeout
        delay = 0.5
        async with httpx.AsyncClient(timeout=5.0) as client:
            while loop.time() < deadline:
                try:
                    resp = await client.get(f"{self._url}/system_stats")
                    if resp.status_code == 200:
                        return True
                except (httpx.TransportError, httpx.TimeoutException):
                    pass
                await asyncio.sleep(delay)
                delay = min(delay * 1.5, 5.0)
        return False

    async def _probe_health(self) -> bool:
        """Single health check request — pass/fail, no retries."""
        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                resp = await client.get(f"{self._url}/system_stats")
                return resp.status_code == 200
            except (httpx.TransportError, httpx.TimeoutException):
                return False

    async def _kill_process(self) -> None:
        """Kill the current managed process and reset state."""
        if not self._process:
            return
        await self._cancel_idle_watcher()
        kill_process_tree(self._process.pid)
        try:
            await asyncio.wait_for(self._process.wait(), timeout=5.0)
        except TimeoutError:
            logger.warning("Process (pid=%d) did not exit within 5s after SIGKILL", self._process.pid)
            await self._process.wait()
        self._process = None
        self._managed = False
        self._remove_pid_file()

    async def _spawn(self) -> dict | None:
        """Spawn ComfyUI subprocess. Returns error dict on failure, None on success."""
        args = shlex.split(self._start_cmd)
        logger.info("Starting ComfyUI: %s", args)
        try:
            self._process = await spawn_subprocess(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except (FileNotFoundError, OSError) as exc:
            logger.error("Failed to start ComfyUI: %s", exc)
            return transient_error(
                "unreachable",
                f"Failed to start ComfyUI: {exc}",
            )

        self._managed = True

        # Verify the process is still alive after spawn
        if not is_process_alive(self._process.pid):
            self._process = None
            self._managed = False
            return transient_error(
                "unreachable",
                "ComfyUI process exited immediately after spawn",
            )

        # Deregister previous atexit handler before registering a new one
        if self._atexit_handler is not None:
            atexit.unregister(self._atexit_handler)

        # Safety net for unclean exits — kill the whole process tree
        spawn_pid = self._process.pid
        spawn_proc = self._process  # capture reference; self._process may change on respawn

        def _atexit_kill():
            if spawn_proc.returncode is None:
                kill_process_tree(spawn_pid)

        self._atexit_handler = _atexit_kill
        atexit.register(_atexit_kill)

        ready = await self._wait_until_ready()
        if not ready:
            logger.error(
                "ComfyUI started but did not become ready within %ds",
                self._start_timeout,
            )
            await self._kill_process()
            return transient_error(
                "unreachable",
                f"ComfyUI started but did not become ready within {self._start_timeout}s",
            )

        logger.info("ComfyUI started and ready (pid=%d)", self._process.pid)
        self._write_pid_file()
        return None

    async def ensure_ready(self) -> dict | None:
        """Ensure ComfyUI is healthy and ready. Returns error dict on failure, None on success.

        Flow:
        1. Probe /system_stats — if healthy, return immediately
        2. If unhealthy and _process exists with returncode set — dead process, cleanup
        3. If unhealthy and _start_cmd is set — spawn and wait for ready
        4. If unhealthy and no _start_cmd — return transient_error
        """
        async with self._lock:
            try:
                # Track activity for idle timeout
                self._last_activity = asyncio.get_running_loop().time()

                # Step 1: Quick health probe
                if await self._probe_health():
                    self._start_idle_watcher()
                    return None

                # Step 2: Clean up dead or hung managed process
                if self._process is not None:
                    if self._process.returncode is not None:
                        logger.warning(
                            "ComfyUI process exited with code %d, re-spawning",
                            self._process.returncode,
                        )
                        self._process = None
                        self._managed = False
                        self._remove_pid_file()
                    elif self._managed:
                        # Process alive but HTTP unresponsive — kill before respawn
                        logger.warning(
                            "ComfyUI process (pid=%d) alive but unresponsive, killing",
                            self._process.pid,
                        )
                        await self._kill_process()

                # Step 3: Spawn if we have a start command
                if self._start_cmd:
                    result = await self._spawn()
                    if result is None:
                        self._start_idle_watcher()
                    return result

                # Step 4: No start command and ComfyUI unreachable
                return transient_error(
                    "unreachable",
                    f"ComfyUI is not reachable at {self._url} and no COMFYUI_START_CMD is configured",
                )
            except Exception as exc:
                logger.exception("Unexpected error in ensure_ready")
                return transient_error(
                    "unreachable",
                    f"Failed to ensure ComfyUI is ready: {type(exc).__name__}: {exc}",
                )

    async def shutdown(self) -> None:
        """Graceful shutdown: SIGTERM → wait 10s → SIGKILL."""
        await self._cancel_idle_watcher()
        if not self._process or self._process.returncode is not None:
            return
        if not is_process_alive(self._process.pid):
            self._process = None
            self._managed = False
            self._remove_pid_file()
            return
        logger.info("Shutting down ComfyUI (pid=%d)", self._process.pid)
        await asyncio.to_thread(graceful_kill, self._process.pid, timeout=10.0)
        await self._process.wait()
        self._managed = False
        self._process = None
        self._remove_pid_file()


async def cleanup_orphan(pid_file) -> None:
    """Kill an orphaned ComfyUI process from a previous crash, if any."""
    if not pid_file.is_file():
        return

    try:
        content = pid_file.read_text().strip()
    except OSError as exc:
        logger.warning("Cannot read PID file %s: %s — removing", pid_file, exc)
        with suppress(OSError):
            pid_file.unlink(missing_ok=True)
        return

    try:
        pid = int(content)
    except ValueError:
        logger.warning("PID file %s contains invalid content: %r — removing", pid_file, content)
        pid_file.unlink(missing_ok=True)
        return

    # Check if process is alive
    if not is_process_alive(pid):
        logger.info("Stale PID file %s (pid=%d already dead) — removing", pid_file, pid)
        pid_file.unlink(missing_ok=True)
        return

    # PID reuse safety (AC #6): verify it's actually ComfyUI
    cmdline = get_process_cmdline(pid)
    if cmdline is None or "comfyui" not in cmdline.lower():
        logger.warning(
            "PID %d is alive but not ComfyUI (cmdline: %r) — removing stale PID file",
            pid,
            cmdline,
        )
        pid_file.unlink(missing_ok=True)
        return

    # Confirmed ComfyUI orphan — kill process tree
    logger.info("Killing orphaned ComfyUI process (pid=%d)", pid)
    await asyncio.to_thread(graceful_kill, pid, timeout=5.0)

    pid_file.unlink(missing_ok=True)
    logger.info("Orphaned ComfyUI process cleaned up")


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """Create ComfyUIManager and yield it in the context dict.

    ComfyUI is NOT started at boot — it will be spawned lazily on the first
    queue_prompt call via manager.ensure_ready().
    """
    try:
        await cleanup_orphan(PID_FILE)
    except Exception:
        logger.warning("Orphan cleanup failed — continuing startup", exc_info=True)
    manager = ComfyUIManager(COMFYUI_URL, COMFYUI_START_CMD, COMFYUI_START_TIMEOUT, COMFYUI_IDLE_TIMEOUT)
    try:
        yield {"comfyui_manager": manager}
    finally:
        await manager.shutdown()


mcp = FastMCP("slop-studio", lifespan=lifespan)


from slop_studio import bluesky, comfyui, templates


@mcp.tool()
@safe_tool
async def list_templates() -> dict:
    """List all available workflow templates with summary metadata.

    Returns template names, models, descriptions, supported aspect ratios,
    and expected generation duration. Use this to discover what image generation
    workflows are available before calling queue_prompt.

    Each template targets a specific model and supports different aspect ratios.
    Choose the template that best matches the user's intent based on the model
    and description.
    """
    return await templates.list_templates()


@mcp.tool()
@safe_tool
async def get_template(template_name: str) -> dict:
    """Inspect a specific template's full metadata including input definitions.

    Returns the complete template configuration: model, description, required
    and optional inputs with their node mappings, supported aspect ratios with
    exact pixel dimensions, and resolution node definitions.

    Call this after list_templates to get detailed input requirements before
    calling queue_prompt. The inputs field shows exactly what parameters the
    template accepts (e.g., prompt text, negative prompt).
    """
    return await templates.get_template(template_name)


@mcp.tool()
@safe_tool
async def add_template(name: str, workflow_json: dict, metadata: dict) -> dict:
    """Add a new workflow template from an exported ComfyUI workflow.

    Saves the workflow JSON and metadata sidecar to the templates directory
    after validating the metadata structure. The template is immediately
    available for use with queue_prompt.

    The metadata must include: model (string) and description (string).
    Optional: inputs (object mapping input names to {node_id, field} definitions),
    aspect_ratios, resolution_nodes, expected_duration.

    Use this when the user has exported a workflow from ComfyUI's browser UI
    and wants to register it as a reusable template. Template names cannot
    contain path characters (/, ..) or start with a dot.
    """
    return await templates.add_template(name, workflow_json, metadata)


@mcp.tool()
@safe_tool
async def update_template(name: str, workflow_json: dict | None = None, metadata: dict | None = None) -> dict:
    """Update an existing workflow template's workflow JSON and/or metadata.

    Overwrites the specified files for an existing template. Provide
    workflow_json to update the workflow, metadata to update the sidecar,
    or both. At least one must be provided. Metadata is validated on write.

    Use this when a template needs to be updated after ComfyUI custom nodes
    change, or to refine template metadata (descriptions, input definitions,
    aspect ratios).
    """
    return await templates.update_template(name, workflow_json, metadata)


@mcp.tool()
@safe_tool
async def delete_template(name: str) -> dict:
    """Delete a workflow template by name.

    Removes both the workflow JSON and metadata sidecar files from the
    templates directory. The template is immediately unavailable for use
    with queue_prompt.

    Use this when a template is outdated, broken, or no longer needed.
    Template names cannot contain path characters (/, ..) or start with
    a dot.
    """
    return await templates.delete_template(name)


# sloppify_prompt is experimental — code lives in slop_studio/sloppify.py
# but is not registered as a tool until stabilized.


@mcp.tool()
@safe_tool
async def queue_prompt(
    template_name: str,
    inputs: dict,
    aspect_ratio: str | None = None,
    ctx: Context = None,
) -> dict:
    """Submit an image generation job using a workflow template.

    Loads the named template, injects your input values into the correct
    workflow nodes, randomizes all seeds to avoid cached results, and submits
    the workflow to ComfyUI. Returns a prompt_id to track the job.

    Call list_templates first to see available templates, then get_template
    to check required inputs. The inputs dict keys must match the template's
    input names (e.g., {"prompt": "a sunset over mountains"}).

    Optional aspect_ratio overrides the default resolution (e.g., "16:9",
    "9:16", "1:1"). Use get_template to see supported aspect ratios.
    """
    manager = ctx.lifespan_context["comfyui_manager"]
    error = await manager.ensure_ready()
    if error:
        return error
    return await comfyui.queue_prompt(template_name, inputs, aspect_ratio)


# check_job is deprecated in favour of check_next_job.
# Code lives in slop_studio/comfyui.py but is no longer registered as a tool.


@mcp.tool()
@safe_tool
async def check_next_job(prompt_ids: list[str], wait: int = 0) -> dict:
    """Poll multiple generation jobs and return all that complete or fail.

    Accepts a list of prompt_ids from previous queue_prompt calls. Polls all
    jobs each cycle and collects every job that finishes within the wait window.
    Failed jobs are retried up to 3 times before being reported.

    Returns completed jobs (with outputs), failed jobs (with errors), and
    remaining prompt_ids still in progress. The caller should call get_image
    for each completed job, then call check_next_job again with the remaining
    IDs until all are resolved.

    Use this instead of check_job when you have multiple jobs queued.
    Avoids redundant pending checks by batching all IDs into one polling loop.
    """
    return await comfyui.check_next_job(prompt_ids, wait)


@mcp.tool()
@safe_tool
async def get_image(prompt_id: str) -> dict | list:
    """Retrieve the output image from a completed generation job.

    Downloads the image from ComfyUI, saves it to the output directory
    organized by date ({output_dir}/{YYYY-MM-DD}/{filename}), and returns
    the absolute file path plus a base64 JPEG thumbnail for inline display.

    The response includes a thumbnail_base64 field containing a small JPEG
    preview that can be embedded as a data:image/jpeg;base64,... URI.

    Call this after check_job returns status 'completed'. If the job is
    still running, call check_job with wait first to poll for completion.
    """
    return await comfyui.get_image(prompt_id)


@mcp.tool()
@safe_tool
async def open_image(file_path: str) -> dict:
    """Open an image file in the OS default viewer.

    Uses 'open' on macOS, 'xdg-open' on Linux, and 'os.startfile' on Windows.
    The file_path must be inside the configured output directory.
    """
    import os
    import platform

    from slop_studio.config import OUTPUT_DIR

    _IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}

    real_path = os.path.realpath(file_path)
    real_output = os.path.realpath(OUTPUT_DIR)
    if not real_path.startswith(real_output + os.sep) and real_path != real_output:
        return {"status": "error", "error": "File must be inside the output directory"}

    ext = os.path.splitext(real_path)[1].lower()
    if ext not in _IMAGE_EXTENSIONS:
        return {"status": "error", "error": f"Unsupported file type: {ext}"}

    if not os.path.isfile(real_path):
        return {"status": "error", "error": f"File not found: {file_path}"}

    system = platform.system()
    try:
        if system == "Darwin":
            await asyncio.create_subprocess_exec(
                "open",
                real_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        elif system == "Linux":
            await asyncio.create_subprocess_exec(
                "xdg-open",
                real_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        elif system == "Windows":
            os.startfile(real_path)
        else:
            return {"status": "error", "error": f"Unsupported platform: {system}"}
    except OSError as exc:
        return {"status": "error", "error": f"Failed to open: {exc}"}

    return {"status": "success", "file_path": real_path}


@mcp.tool()
@safe_tool
async def open_gallery(file_paths: list[str]) -> dict:
    """Open multiple images in a single HTML gallery page.

    Generates a lightweight HTML file with a dark-themed responsive grid and
    click-to-lightbox, then opens it in the default browser. Use this instead
    of calling open_image multiple times when viewing a batch of images.

    Args:
        file_paths: List of absolute paths to image files. All must be
                    inside the configured output directory.
    """
    import os
    import platform

    from slop_studio.config import OUTPUT_DIR
    from slop_studio.gallery import generate_gallery

    _IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}

    real_output = os.path.realpath(OUTPUT_DIR)

    validated_paths = []
    for file_path in file_paths:
        real_path = os.path.realpath(file_path)
        if not real_path.startswith(real_output + os.sep) and real_path != real_output:
            return {"status": "error", "error": f"File must be inside the output directory: {file_path}"}
        ext = os.path.splitext(real_path)[1].lower()
        if ext not in _IMAGE_EXTENSIONS:
            return {"status": "error", "error": f"Unsupported file type: {ext}"}
        if not os.path.isfile(real_path):
            return {"status": "error", "error": f"File not found: {file_path}"}
        validated_paths.append(real_path)

    gallery_path = await asyncio.to_thread(generate_gallery, validated_paths, OUTPUT_DIR)

    system = platform.system()
    try:
        if system == "Darwin":
            await asyncio.create_subprocess_exec(
                "open",
                gallery_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        elif system == "Linux":
            await asyncio.create_subprocess_exec(
                "xdg-open",
                gallery_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        elif system == "Windows":
            os.startfile(gallery_path)
        else:
            return {"status": "error", "error": f"Unsupported platform: {system}"}
    except OSError as exc:
        return {"status": "error", "error": f"Failed to open gallery: {exc}"}

    return {"status": "success", "gallery_path": gallery_path, "image_count": len(validated_paths)}


@mcp.tool()
@safe_tool
async def post_to_bluesky(
    text: str,
    image_path: str | None = None,
    alt_text: str = "",
    tags: list[str] | None = None,
    images: list[dict] | None = None,
) -> dict:
    """Post generated image(s) to Bluesky (up to 4).

    Uploads image(s) and creates a post with the given text. Hashtags are
    rendered as proper AT Protocol tag facets (clickable and searchable).
    Images over 1 MB are automatically compressed to JPEG.

    Provide EITHER image_path + alt_text for a single image, OR images for
    multiple. Do not provide both.

    Requires BSKY_HANDLE and BSKY_APP_PASSWORD environment variables.
    Create an app password at bsky.app > Settings > App Passwords.

    Args:
        text: Post text (max 300 characters including tags).
        image_path: Absolute path to a single image file (legacy).
        alt_text: Alt text for the single image_path.
        tags: Optional hashtags without #. e.g. ["aiart", "comfyui"]
        images: List of image dicts, each with "path" and "alt_text" keys.
                Up to 4. e.g. [{"path": "/out/a.png", "alt_text": "desc"}]
    """
    return await bluesky.post_image(
        image_path=image_path,
        text=text,
        alt_text=alt_text,
        tags=tags,
        images=images,
    )


if __name__ == "__main__":
    mcp.run()
