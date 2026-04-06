import asyncio
import atexit
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import functools
import logging
import os
import shlex
import signal

import httpx
from fastmcp import FastMCP

from slop_studio.config import COMFYUI_START_CMD, COMFYUI_START_TIMEOUT, COMFYUI_URL
from slop_studio.errors import transient_error

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
                f"An internal error occurred in {func.__name__}: "
                f"{type(exc).__name__}: {exc}",
            )
    return wrapper


async def _wait_for_comfyui(url: str, timeout: float) -> bool:
    """Poll ComfyUI until it responds, using exponential backoff.

    Tries /ready first (ComfyUI 0.3.x+), falls back to /system_stats.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    delay = 0.5
    async with httpx.AsyncClient(timeout=5.0) as client:
        while loop.time() < deadline:
            try:
                resp = await client.get(f"{url}/system_stats")
                if resp.status_code == 200:
                    return True
            except httpx.TransportError:
                pass
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 5.0)
    return False


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """Validate ComfyUI connectivity before accepting requests.

    When COMFYUI_START_CMD is set, spawns ComfyUI as a managed child process
    and waits for it to become ready before proceeding.
    """
    process = None

    if COMFYUI_START_CMD:
        # Skip spawn if ComfyUI is already reachable
        already_running = False
        async with httpx.AsyncClient(timeout=5.0) as probe:
            try:
                resp = await probe.get(f"{COMFYUI_URL}/system_stats")
                if resp.status_code == 200:
                    already_running = True
                    logger.info("ComfyUI already running at %s, skipping spawn", COMFYUI_URL)
            except httpx.TransportError:
                pass

        if not already_running:
            args = shlex.split(COMFYUI_START_CMD)
            logger.info("Starting ComfyUI: %s", args)
            try:
                process = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=True,
                )
            except (FileNotFoundError, OSError) as exc:
                logger.error("Failed to start ComfyUI: %s", exc)
                raise

            # Safety net for unclean exits — kill the whole process group
            pgid = os.getpgid(process.pid)

            def _atexit_kill():
                if process.returncode is None:
                    try:
                        os.killpg(pgid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass

            atexit.register(_atexit_kill)

            ready = await _wait_for_comfyui(COMFYUI_URL, COMFYUI_START_TIMEOUT)
            if not ready:
                try:
                    os.killpg(pgid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                await process.wait()
                raise RuntimeError(
                    f"ComfyUI started but did not become ready within {COMFYUI_START_TIMEOUT}s"
                )
            logger.info("ComfyUI started and ready (pid=%d)", process.pid)

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(f"{COMFYUI_URL}/system_stats")
            response.raise_for_status()
        except httpx.ConnectError:
            logger.error("ComfyUI is unreachable at %s", COMFYUI_URL)
            raise
        except httpx.TimeoutException:
            logger.error("ComfyUI connection timed out at %s", COMFYUI_URL)
            raise
        except httpx.HTTPStatusError as exc:
            logger.error(
                "ComfyUI returned HTTP %d at %s",
                exc.response.status_code,
                COMFYUI_URL,
            )
            raise
    logger.info("ComfyUI reachable at %s", COMFYUI_URL)

    try:
        yield {}
    finally:
        if process and process.returncode is None:
            pgid = os.getpgid(process.pid)
            logger.info("Shutting down ComfyUI process group (pgid=%d)", pgid)
            try:
                os.killpg(pgid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("ComfyUI did not exit gracefully, killing")
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()


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
async def update_template(
    name: str, workflow_json: dict | None = None, metadata: dict | None = None
) -> dict:
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
    template_name: str, inputs: dict, aspect_ratio: str | None = None
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
    an inline JPEG thumbnail preview alongside the full-resolution file path.

    The response contains an ImageContent block (base64 JPEG thumbnail for
    inline display) and a TextContent block with JSON metadata including
    the absolute file path. If thumbnail generation fails, only the
    TextContent block is returned (the full-res image is always saved).

    Call this after check_job returns status 'completed'. If the job is
    still running, call check_job with wait first to poll for completion.
    """
    return await comfyui.get_image(prompt_id)


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
