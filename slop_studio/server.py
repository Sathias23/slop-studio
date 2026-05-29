import asyncio
import atexit
import functools
import importlib.metadata
import logging
import os
import platform
import shlex
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import httpx
from fastmcp import FastMCP
from fastmcp.server.context import Context

from slop_studio.config import (
    COMFYUI_IDLE_TIMEOUT,
    COMFYUI_START_CMD,
    COMFYUI_START_TIMEOUT,
    COMFYUI_URL,
    OUTPUT_DIR,
    PID_FILE,
)
from slop_studio.errors import terminal_error, transient_error
from slop_studio.process import (
    get_process_cmdline,
    graceful_kill,
    is_process_alive,
    kill_process_tree,
    spawn_subprocess,
)

logger = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}

_COMFY_CLOUD_PORTAL_URL = "https://platform.comfy.org/"


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
            try:
                await asyncio.wait_for(self._process.wait(), timeout=10.0)
            except TimeoutError:
                logger.error("Process (pid=%d) still alive 10s after SIGKILL — giving up", self._process.pid)
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
        async with self._lock:
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


from slop_studio import bluesky, models, templates
from slop_studio.backends import router


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
async def check_requirements(template_name: str) -> dict:
    """Check whether a local-backend template's declared model files are present on disk.

    Reads the template's ``model_requirements`` (if any) and reports which
    files exist under the configured ComfyUI models directory and which
    are missing. Read-only — never touches the network and never writes.

    Call this BEFORE ``queue_prompt`` for local-backend templates the user
    has not generated with before. If ``missing`` is non-empty, surface
    each entry's ``filename``, ``size_bytes``, and ``url`` to the user
    along with the total download size, then call ``download_models``
    only after the user confirms.

    Templates without ``model_requirements`` (the legacy default) return
    ``{status: "success", present: [], missing: [], note: "..."}``.
    """
    return await models.check_requirements(template_name)


@mcp.tool()
@safe_tool
async def download_models(template_name: str) -> dict:
    """Download every missing model declared by a template into ComfyUI's models directory.

    For each entry in ``model_requirements`` not already on disk, streams
    the URL into ``<target>.partial``, hashes inline against any declared
    ``sha256``, and atomic-renames on success. Failures (network error,
    hash mismatch, auth failure) clean up the ``.partial`` before
    returning a structured error.

    Auth tokens, when required by an entry's ``auth`` field
    (``"huggingface"`` or ``"civitai"``), are read from env vars or
    ``~/.config/slop-studio/credentials.json`` and ride in
    ``Authorization: Bearer <token>`` — never logged.

    IMPORTANT: only call this AFTER ``check_requirements`` has surfaced
    the missing items and their total size to the user, and the user has
    confirmed they want to proceed. Downloads can be multi-gigabyte.

    Returns ``{status: "success", downloaded: [...], skipped: [...]}`` on
    success. Templates with no declared requirements return the no-op
    shape with a ``note`` field.
    """
    return await models.download_models(template_name)


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
    return await router.route_submission(
        template_name,
        inputs,
        aspect_ratio,
        lifecycle_manager=manager,
    )


# check_job is deprecated in favour of check_next_job — code lives in slop_studio/backends/local.py.


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
    return await router.check_next_job(prompt_ids, wait)


@mcp.tool()
@safe_tool
async def get_image(prompt_id: str, include_base64: bool = False) -> dict | list:
    """Retrieve the output image from a completed generation job.

    Downloads the image from ComfyUI, saves it to the output directory
    organized by date ({output_dir}/{YYYY-MM-DD}/{filename}), and returns
    the absolute file path.

    Set include_base64 to true to also receive a thumbnail_base64 field
    containing a small JPEG preview for inline display (useful for clients
    like Claude Desktop that can render embedded images).

    Call this after check_job returns status 'completed'. If the job is
    still running, call check_job with wait first to poll for completion.
    """
    return await router.get_image(prompt_id, include_base64=include_base64)


@mcp.tool()
@safe_tool
async def open_gallery(file_paths: str | list[str]) -> dict:
    """Open image(s) for viewing.

    Accepts a single image path or a list. When given one image, opens it
    directly in the OS default viewer (Preview on macOS, etc.). When given
    multiple images, generates a lightweight HTML gallery with a dark-themed
    responsive grid and click-to-lightbox, then opens it in the default browser.

    Args:
        file_paths: Absolute path to an image file, or a list of paths.
                    All must be inside the configured output directory.
    """
    if isinstance(file_paths, str):
        file_paths = [file_paths]

    if not file_paths:
        return terminal_error("invalid_inputs", "At least one file path is required")

    output_root = Path(OUTPUT_DIR).resolve()

    validated_paths = []
    for file_path in file_paths:
        p = Path(file_path).resolve()
        try:
            p.relative_to(output_root)
        except ValueError:
            return terminal_error("invalid_path", f"File must be inside the output directory: {file_path}")
        if p.suffix.lower() not in _IMAGE_EXTENSIONS:
            return terminal_error("invalid_inputs", f"Unsupported file type: {p.suffix.lower()}")
        if not p.is_file():
            return terminal_error("invalid_path", f"File not found: {file_path}")
        validated_paths.append(str(p))

    if len(validated_paths) == 1:
        target = validated_paths[0]
    else:
        from slop_studio.gallery import generate_gallery

        target = await asyncio.to_thread(generate_gallery, validated_paths, OUTPUT_DIR)

    system = platform.system()
    try:
        if system == "Darwin":
            await asyncio.create_subprocess_exec(
                "open",
                target,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        elif system == "Linux":
            await asyncio.create_subprocess_exec(
                "xdg-open",
                target,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        elif system == "Windows":
            await asyncio.to_thread(os.startfile, target)
        else:
            return terminal_error("invalid_inputs", f"Unsupported platform: {system}")
    except OSError as exc:
        return transient_error("open_failed", f"Failed to open: {exc}")

    if len(validated_paths) == 1:
        return {"status": "success", "file_path": validated_paths[0]}
    return {"status": "success", "gallery_path": target, "image_count": len(validated_paths)}


@mcp.tool()
@safe_tool
async def open_comfy_cloud_portal() -> dict:
    """Open the Comfy Cloud billing/account portal in the default browser.

    Opens https://platform.comfy.org/ so users can top up credits, manage
    API keys, or resolve account issues. Requires no authentication and
    no API key — this is a pure URL opener.

    Call this in response to ``no_credits``, ``auth_failed``, or
    ``account_issue`` errors from cloud jobs. The relevant error messages
    already name this tool by name as the recommended next step.
    """
    system = platform.system()
    try:
        if system == "Darwin":
            await asyncio.create_subprocess_exec(
                "open",
                _COMFY_CLOUD_PORTAL_URL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        elif system == "Linux":
            await asyncio.create_subprocess_exec(
                "xdg-open",
                _COMFY_CLOUD_PORTAL_URL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        elif system == "Windows":
            await asyncio.to_thread(os.startfile, _COMFY_CLOUD_PORTAL_URL)
        else:
            return terminal_error("invalid_inputs", f"Unsupported platform: {system}")
    except OSError as exc:
        return transient_error("open_failed", f"Failed to open: {exc}")

    return {"status": "success", "url": _COMFY_CLOUD_PORTAL_URL}


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


_ISSUE_URL = "https://github.com/Sathias23/slop-studio/issues"
_ISSUE_CHECKLIST = (
    "slop-studio version (provided in this response under `version`)",
    "What you tried (the prompt + which template, if relevant)",
    "What happened (paste the `error_type` and `error` message from the failing tool response)",
    "OS (macOS / Linux / Windows)",
    "Backend used (local vs cloud), and whether ComfyUI was managed by slop-studio (COMFYUI_START_CMD set) or external",
)


@mcp.tool()
@safe_tool
async def report_issue() -> dict:
    """Surface the canonical bug-report URL + checklist for filing a slop-studio issue.

    Call this when the user asks how to file a bug, raise an issue, or report a problem
    with slop-studio itself — e.g. "how do I report this?", "where do I file a bug?",
    "this looks like a slop-studio bug". The response gives you the GitHub issue URL and
    a checklist of details to gather from the user before pointing them at the link, so
    the maintainer can reproduce and triage.

    **Call this for slop-studio's own behavior:** tool errors (`error_type` set in a tool
    response), MCP integration issues, template validation failures, model-download
    problems, unexpected exceptions, surprising tool semantics.

    **Do NOT call this for generation-quality complaints** (blurry images, wrong aspect
    ratio, weak prompt adherence, color cast) — those are upstream issues with ComfyUI
    or the model, not slop-studio. The user's recourse there is a different model,
    template, or upstream report.

    Returns ``{status, issue_url, version, checklist, note}``. ``version`` is read
    dynamically from the installed package metadata; falls back to ``"unknown"`` with
    an explanatory ``note`` if metadata can't be resolved (e.g. running from raw source
    or corrupt dist-info). ``note`` is ``None`` on the happy path.
    """
    try:
        version = importlib.metadata.version("slop-studio")
        return {
            "status": "success",
            "issue_url": _ISSUE_URL,
            "version": version,
            "checklist": list(_ISSUE_CHECKLIST),
            "note": None,
        }
    except importlib.metadata.PackageNotFoundError:
        note = (
            "Package metadata for 'slop-studio' was not found — likely running from "
            "an uninstalled source tree. Check the `version` field in `pyproject.toml` "
            "or `manifest.json` for the version string to include in the issue."
        )
    except Exception as exc:
        note = (
            f"Could not resolve slop-studio version from package metadata "
            f"({type(exc).__name__}: {exc}). Check `pyproject.toml` or `manifest.json`."
        )
    return {
        "status": "success",
        "issue_url": _ISSUE_URL,
        "version": "unknown",
        "checklist": list(_ISSUE_CHECKLIST),
        "note": note,
    }


if __name__ == "__main__":
    mcp.run()
