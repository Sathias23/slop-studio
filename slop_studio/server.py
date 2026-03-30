from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging

import httpx
from fastmcp import FastMCP

from slop_studio.config import COMFYUI_URL

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """Validate ComfyUI connectivity before accepting requests."""
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
    yield {}


mcp = FastMCP("slop-studio", lifespan=lifespan)


from slop_studio import comfyui, templates


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
async def check_job(prompt_id: str, wait: int = 0) -> dict:
    """Check the status of a submitted image generation job.

    Returns the current job status: pending (queued), running (processing),
    completed (with output details), or failed (with error message).

    By default performs a single non-blocking check. Set wait (in seconds)
    to poll until completion or timeout. Polls every 3 seconds, capped at
    45 seconds maximum.

    After queue_prompt returns a prompt_id, call this with wait=30 to poll
    for completion. If status is still 'running', call again. Once status
    is 'completed', call get_image to retrieve the output file path.
    """
    return await comfyui.check_job(prompt_id, wait)


@mcp.tool()
async def get_image(prompt_id: str) -> dict:
    """Retrieve the output image from a completed generation job.

    Downloads the image from ComfyUI, saves it to the output directory
    organized by date ({output_dir}/{YYYY-MM-DD}/{filename}), and returns
    the absolute file path.

    Call this after check_job returns status 'completed'. If the job is
    still running, call check_job with wait first to poll for completion.
    """
    return await comfyui.get_image(prompt_id)
