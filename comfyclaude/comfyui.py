import asyncio
import copy
import json
import logging
import os
import random
from datetime import date
from pathlib import Path

import httpx

from comfyclaude.config import COMFYUI_URL, OUTPUT_DIR, TEMPLATES_DIR
from comfyclaude.errors import terminal_error, transient_error

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 3  # seconds between polls (FR16)
MAX_POLL_DURATION = 45     # maximum total polling time in seconds (FR16)


def _inject_inputs(workflow: dict, meta_inputs: dict, user_inputs: dict) -> None:
    """Inject user-provided input values into the workflow nodes in-place."""
    for input_name, value in user_inputs.items():
        if input_name not in meta_inputs:
            continue
        input_def = meta_inputs[input_name]
        node_id = input_def["node_id"]
        field = input_def["field"]
        workflow[node_id]["inputs"][field] = value


def _randomize_seeds(workflow: dict) -> None:
    """Replace all seed/noise_seed fields with random values to prevent cache hits."""
    for node in workflow.values():
        inputs = node.get("inputs", {})
        for key in ("seed", "noise_seed"):
            if key in inputs and isinstance(inputs[key], int):
                inputs[key] = random.randint(0, 2**63 - 1)


def _inject_resolution(workflow: dict, meta: dict, aspect_ratio: str | None) -> None:
    """Map aspect ratio label to dimensions and inject into resolution nodes."""
    if aspect_ratio is None:
        return

    dims = meta["aspect_ratios"][aspect_ratio]
    for res_node in meta.get("resolution_nodes", []):
        node_id = res_node["node_id"]
        workflow[node_id]["inputs"][res_node["width_field"]] = dims["width"]
        workflow[node_id]["inputs"][res_node["height_field"]] = dims["height"]


async def queue_prompt(
    template_name: str, inputs: dict, aspect_ratio: str | None = None
) -> dict:
    """Load a template, inject inputs, randomize seeds, and submit to ComfyUI."""
    workflow_path = Path(TEMPLATES_DIR) / f"{template_name}.json"
    meta_path = Path(TEMPLATES_DIR) / f"{template_name}.meta.json"

    if not workflow_path.is_file() or not meta_path.is_file():
        return terminal_error(
            "invalid_inputs", f"Template '{template_name}' not found"
        )

    try:
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return terminal_error(
            "invalid_inputs", f"Failed to read template '{template_name}': {exc}"
        )

    if not isinstance(workflow, dict):
        return terminal_error(
            "invalid_inputs",
            f"Template '{template_name}' workflow is not a JSON object",
        )

    # Validate required inputs
    meta_inputs = meta.get("inputs", {})
    for input_name, input_def in meta_inputs.items():
        if input_def.get("type") == "required" and input_name not in inputs:
            return terminal_error(
                "invalid_inputs",
                f"Missing required input '{input_name}': {input_def.get('description', '')}",
            )

    # Validate aspect ratio
    if aspect_ratio is not None and aspect_ratio not in meta.get("aspect_ratios", {}):
        supported = list(meta.get("aspect_ratios", {}).keys())
        return terminal_error(
            "invalid_inputs",
            f"Unsupported aspect ratio '{aspect_ratio}'. Supported: {supported}",
        )

    # Prepare workflow
    prepared = copy.deepcopy(workflow)
    _inject_inputs(prepared, meta_inputs, inputs)
    _randomize_seeds(prepared)
    _inject_resolution(prepared, meta, aspect_ratio)

    # Submit to ComfyUI
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{COMFYUI_URL}/prompt",
                json={"prompt": prepared},
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code >= 500:
            return transient_error(
                "unreachable",
                f"ComfyUI returned {exc.response.status_code} at {COMFYUI_URL}",
            )
        error_body = exc.response.text
        return terminal_error(
            "invalid_workflow",
            f"ComfyUI rejected the workflow: {error_body[:500]}",
        )
    except httpx.RequestError:
        return transient_error(
            "unreachable", f"Cannot connect to ComfyUI at {COMFYUI_URL}"
        )

    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError):
        return terminal_error(
            "invalid_workflow", "ComfyUI returned a non-JSON response"
        )

    prompt_id = data.get("prompt_id")
    if prompt_id is None:
        return terminal_error(
            "invalid_workflow",
            f"ComfyUI response missing prompt_id: {str(data)[:200]}",
        )
    return {"status": "success", "prompt_id": prompt_id}


async def _fetch_job_status(prompt_id: str) -> dict:
    """Fetch job status from ComfyUI history API.

    Returns:
        {"state": "pending"} if prompt_id not in history
        {"state": "running"} if job is in history but not completed
        {"state": "completed", "outputs": {...}} if job finished successfully
        {"state": "failed", "error": "..."} if job failed
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(f"{COMFYUI_URL}/history/{prompt_id}")
        response.raise_for_status()

    data = response.json()

    if prompt_id not in data:
        return {"state": "pending"}

    job_data = data[prompt_id]
    status_info = job_data.get("status", {})
    status_str = status_info.get("status_str", "")
    completed = status_info.get("completed", False)

    if status_str == "error":
        messages = status_info.get("messages", [])
        error_msg = "Job failed in ComfyUI"
        for msg in messages:
            if isinstance(msg, list) and len(msg) >= 2 and msg[0] == "execution_error":
                error_detail = msg[1] if isinstance(msg[1], str) else str(msg[1])
                error_msg = f"Job failed: {error_detail[:500]}"
                break
        return {"state": "failed", "error": error_msg}

    if completed and status_str == "success":
        outputs = job_data.get("outputs", {})
        return {"state": "completed", "outputs": outputs}

    return {"state": "running"}


def _format_result(prompt_id: str, result: dict) -> dict:
    """Convert internal status to MCP tool response format."""
    state = result["state"]

    if state == "completed":
        return {
            "status": "completed",
            "prompt_id": prompt_id,
            "outputs": result.get("outputs", {}),
        }

    if state == "failed":
        error_msg = result.get("error", "Job failed in ComfyUI")
        return terminal_error("generation_failed", error_msg)

    # pending or running
    return {"status": state, "prompt_id": prompt_id}


async def check_job(prompt_id: str, wait: int = 0) -> dict:
    """Check job status, optionally polling until completion or timeout."""
    effective_wait = min(wait, MAX_POLL_DURATION)  # Cap at 45s

    try:
        result = await _fetch_job_status(prompt_id)
    except (httpx.ConnectError, httpx.TimeoutException):
        return transient_error("unreachable",
            f"Cannot connect to ComfyUI at {COMFYUI_URL}")
    except httpx.HTTPStatusError as exc:
        return transient_error("unreachable",
            f"ComfyUI returned HTTP {exc.response.status_code}")

    # Non-blocking check or terminal state
    if effective_wait <= 0 or result["state"] in ("completed", "failed"):
        return _format_result(prompt_id, result)

    # Polling loop
    elapsed = 0.0
    while elapsed < effective_wait:
        await asyncio.sleep(DEFAULT_POLL_INTERVAL)
        elapsed += DEFAULT_POLL_INTERVAL

        try:
            result = await _fetch_job_status(prompt_id)
        except (httpx.ConnectError, httpx.TimeoutException):
            return transient_error("unreachable",
                f"Cannot connect to ComfyUI at {COMFYUI_URL}")
        except httpx.HTTPStatusError as exc:
            return transient_error("unreachable",
                f"ComfyUI returned HTTP {exc.response.status_code}")

        if result["state"] in ("completed", "failed"):
            return _format_result(prompt_id, result)

    # Timeout reached, still running/pending
    return _format_result(prompt_id, result)


async def get_image(prompt_id: str) -> dict:
    """Retrieve completed image, save to output directory, return absolute path."""
    # 1. Check job status
    try:
        result = await _fetch_job_status(prompt_id)
    except (httpx.ConnectError, httpx.TimeoutException):
        return transient_error("unreachable",
            f"Cannot connect to ComfyUI at {COMFYUI_URL}")
    except httpx.HTTPStatusError as exc:
        return transient_error("unreachable",
            f"ComfyUI returned HTTP {exc.response.status_code}")

    state = result["state"]

    if state == "pending":
        return terminal_error("invalid_inputs",
            f"Job {prompt_id} is still pending (queued, not started)")
    if state == "running":
        return terminal_error("invalid_inputs",
            f"Job {prompt_id} is still running. Call check_job with wait to poll for completion first.")
    if state == "failed":
        error_msg = result.get("error", "Job failed in ComfyUI")
        return terminal_error("generation_failed", error_msg)

    # state == "completed"
    outputs = result.get("outputs", {})

    # 2. Find first image in outputs
    filename = None
    subfolder = ""
    for node_output in outputs.values():
        images = node_output.get("images", [])
        if images:
            filename = images[0].get("filename")
            subfolder = images[0].get("subfolder", "")
            break

    if not filename:
        return terminal_error("completed_no_output",
            f"Job {prompt_id} completed but produced no output images")

    # 3. Sanitize filename (FR21)
    safe_filename = os.path.basename(filename)
    if not safe_filename:
        return terminal_error("completed_no_output",
            f"Job {prompt_id} produced an invalid filename")

    # 4. Fetch image bytes from ComfyUI
    params = {"filename": filename, "type": "output"}
    if subfolder:
        params["subfolder"] = subfolder

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{COMFYUI_URL}/view", params=params)
            response.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException):
        return transient_error("unreachable",
            f"Cannot connect to ComfyUI at {COMFYUI_URL}")
    except httpx.HTTPStatusError as exc:
        return transient_error("unreachable",
            f"ComfyUI returned HTTP {exc.response.status_code} fetching image")

    image_bytes = response.content

    # 5. Save to date-organized output directory
    date_str = date.today().isoformat()  # YYYY-MM-DD
    date_dir = os.path.join(OUTPUT_DIR, date_str)

    try:
        os.makedirs(date_dir, exist_ok=True)
    except OSError as exc:
        return transient_error("storage_error",
            f"Cannot create output directory '{date_dir}': {exc}")

    output_path = os.path.join(date_dir, safe_filename)
    try:
        with open(output_path, "wb") as f:
            f.write(image_bytes)
    except OSError as exc:
        return transient_error("storage_error",
            f"Cannot write image to '{output_path}': {exc}")

    abs_path = os.path.abspath(output_path)
    logger.info("Image saved: %s", abs_path)

    return {"status": "success", "file_path": abs_path, "prompt_id": prompt_id}
