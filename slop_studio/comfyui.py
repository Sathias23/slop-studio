import asyncio
import base64
import copy
import io
import json
import logging
import os
import random
import time
import uuid
from datetime import date
from pathlib import Path

import httpx
from PIL import Image

from slop_studio.config import COMFYUI_URL, OUTPUT_DIR, TEMPLATES_DIR
from slop_studio.errors import terminal_error, transient_error

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 3  # seconds between polls (FR16)
MAX_POLL_DURATION = 45  # maximum total polling time in seconds (FR16)
MAX_FAILURE_RETRIES = 3  # retry failed jobs this many times before reporting


def generate_thumbnail(image_bytes: bytes, max_size: int = 256, quality: int = 50) -> str:
    """Generate a base64-encoded JPEG thumbnail from raw image bytes.

    Resizes to fit within max_size x max_size pixels (preserving aspect ratio,
    never upscaling). Converts non-RGB modes (RGBA, P, L) to RGB for JPEG.

    Args:
        image_bytes: Raw image data (any PIL-supported format).
        max_size: Maximum dimension in pixels (default 256).
        quality: JPEG compression quality 1-95 (default 50).

    Returns:
        Base64-encoded JPEG string (no data URI prefix).

    Raises:
        PIL.UnidentifiedImageError: If image_bytes is not a valid image.
        ValueError: If image_bytes is empty.
    """
    if not image_bytes:
        raise ValueError("image_bytes is empty")

    img = Image.open(io.BytesIO(image_bytes))

    if img.mode != "RGB":
        img = img.convert("RGB")

    img.thumbnail((max_size, max_size))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("ascii")


async def _upload_image(file_path: str) -> str:
    """Upload a local image file to ComfyUI's input directory.

    Returns the ComfyUI filename for use in LoadImage nodes.
    Raises ValueError for invalid/missing files, httpx errors for upload failures.
    """
    if not os.path.isfile(file_path):
        raise ValueError(f"Image file not found: {file_path}")

    try:
        with Image.open(file_path) as img:
            img.verify()
    except Exception as exc:
        raise ValueError(f"File is not a valid image: {file_path}") from exc

    ext = os.path.splitext(file_path)[1].lower() or ".png"
    mime_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
    }
    mime_type = mime_types.get(ext, "application/octet-stream")
    upload_name = f"{uuid.uuid4().hex[:12]}{ext}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        with open(file_path, "rb") as f:
            resp = await client.post(
                f"{COMFYUI_URL}/upload/image",
                files={"image": (upload_name, f, mime_type)},
                data={"type": "input", "overwrite": "true"},
            )
        resp.raise_for_status()

    data = resp.json()
    if "name" not in data:
        raise ValueError(f"ComfyUI upload response missing 'name' key: {str(data)[:200]}")
    return data["name"]


async def _inject_inputs(workflow: dict, meta_inputs: dict, user_inputs: dict) -> None:
    """Inject user-provided input values into the workflow nodes in-place.

    For inputs with input_type: "image", uploads the file to ComfyUI first
    and injects the returned filename.
    """
    for input_name, value in user_inputs.items():
        if input_name not in meta_inputs:
            continue
        input_def = meta_inputs[input_name]
        node_id = input_def.get("node_id")
        field = input_def.get("field")
        if not node_id or not field:
            logger.error("Incomplete input definition for '%s': missing node_id or field", input_name)
            continue
        if node_id not in workflow:
            logger.error("Node '%s' referenced by input '%s' not found in workflow", node_id, input_name)
            continue
        if "inputs" not in workflow[node_id]:
            logger.error("Node '%s' has no 'inputs' key in workflow", node_id)
            continue

        if input_def.get("input_type") == "image":
            value = await _upload_image(value)

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

    aspect_ratios = meta.get("aspect_ratios", {})
    if aspect_ratio not in aspect_ratios:
        logger.error("Aspect ratio '%s' not found in meta (available: %s)", aspect_ratio, list(aspect_ratios.keys()))
        return

    dims = aspect_ratios[aspect_ratio]
    for res_node in meta.get("resolution_nodes", []):
        node_id = res_node.get("node_id")
        width_field = res_node.get("width_field")
        height_field = res_node.get("height_field")
        if not node_id or not width_field or not height_field:
            logger.error("Incomplete resolution_node definition: %s", res_node)
            continue
        if node_id not in workflow:
            logger.error("Resolution node '%s' not found in workflow", node_id)
            continue
        if "inputs" not in workflow[node_id]:
            logger.error("Resolution node '%s' has no 'inputs' key in workflow", node_id)
            continue
        workflow[node_id]["inputs"][width_field] = dims["width"]
        workflow[node_id]["inputs"][height_field] = dims["height"]


async def queue_prompt(template_name: str, inputs: dict, aspect_ratio: str | None = None) -> dict:
    """Load a template, inject inputs, randomize seeds, and submit to ComfyUI."""
    workflow_path = Path(TEMPLATES_DIR) / f"{template_name}.json"
    meta_path = Path(TEMPLATES_DIR) / f"{template_name}.meta.json"

    if not workflow_path.is_file() or not meta_path.is_file():
        return terminal_error("invalid_inputs", f"Template '{template_name}' not found")

    try:
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return terminal_error("invalid_inputs", f"Failed to read template '{template_name}': {exc}")

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
    try:
        await _inject_inputs(prepared, meta_inputs, inputs)
    except ValueError as exc:
        return terminal_error("validation", str(exc))
    except httpx.RequestError:
        return transient_error(
            "unreachable",
            f"Cannot upload image to ComfyUI at {COMFYUI_URL}",
        )
    except httpx.HTTPStatusError as exc:
        return transient_error(
            "unreachable",
            f"ComfyUI image upload returned HTTP {exc.response.status_code}",
        )
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
        return transient_error("unreachable", f"Cannot connect to ComfyUI at {COMFYUI_URL}")

    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError):
        return terminal_error("invalid_workflow", "ComfyUI returned a non-JSON response")

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

    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError):
        return {"state": "failed", "error": "ComfyUI returned non-JSON response from /history"}

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
        return transient_error("unreachable", f"Cannot connect to ComfyUI at {COMFYUI_URL}")
    except httpx.HTTPStatusError as exc:
        return transient_error("unreachable", f"ComfyUI returned HTTP {exc.response.status_code}")

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
            return transient_error("unreachable", f"Cannot connect to ComfyUI at {COMFYUI_URL}")
        except httpx.HTTPStatusError as exc:
            return transient_error("unreachable", f"ComfyUI returned HTTP {exc.response.status_code}")

        if result["state"] in ("completed", "failed"):
            return _format_result(prompt_id, result)

    # Timeout reached, still running/pending
    return _format_result(prompt_id, result)


async def check_next_job(prompt_ids: list[str], wait: int = 0) -> dict:
    """Poll multiple jobs, return all that complete/fail within the wait window."""
    if not prompt_ids:
        return terminal_error("invalid_input", "prompt_ids list is empty")

    effective_wait = min(wait, MAX_POLL_DURATION)
    remaining = list(dict.fromkeys(prompt_ids))  # deduplicate, preserve order
    failure_counts: dict[str, int] = {}
    completed: list[dict] = []
    failed: list[dict] = []

    async def _poll_cycle():
        """Poll all remaining IDs, collect completed/failed."""
        still_remaining = []
        for pid in list(remaining):
            try:
                result = await _fetch_job_status(pid)
            except (httpx.ConnectError, httpx.TimeoutException):
                still_remaining.append(pid)
                return transient_error("unreachable", f"Cannot connect to ComfyUI at {COMFYUI_URL}")
            except httpx.HTTPStatusError as exc:
                still_remaining.append(pid)
                return transient_error("unreachable", f"ComfyUI returned HTTP {exc.response.status_code}")

            if result["state"] == "completed":
                completed.append(
                    {
                        "prompt_id": pid,
                        "outputs": result.get("outputs", {}),
                    }
                )
            elif result["state"] == "failed":
                failure_counts[pid] = failure_counts.get(pid, 0) + 1
                if failure_counts[pid] >= MAX_FAILURE_RETRIES:
                    failed.append(
                        {
                            "prompt_id": pid,
                            "error": result.get("error", "Job failed in ComfyUI"),
                        }
                    )
                else:
                    still_remaining.append(pid)
            else:
                still_remaining.append(pid)
        remaining.clear()
        remaining.extend(still_remaining)
        return None  # no error

    # Initial poll
    err = await _poll_cycle()
    if err is not None:
        return err

    if completed or failed or effective_wait <= 0:
        return _build_batch_result(completed, failed, remaining)

    # Polling loop — use wall clock to avoid overshoot from slow cycles
    deadline = time.monotonic() + effective_wait
    while time.monotonic() < deadline:
        await asyncio.sleep(DEFAULT_POLL_INTERVAL)

        err = await _poll_cycle()
        if err is not None:
            return err

        if completed or failed:
            return _build_batch_result(completed, failed, remaining)

    # Timeout — nothing resolved
    return {
        "status": "waiting",
        "completed": [],
        "failed": [],
        "remaining": list(remaining),
    }


def _build_batch_result(completed: list[dict], failed: list[dict], remaining: list[str]) -> dict:
    """Build the response dict for check_next_job."""
    return {
        "status": "completed",
        "completed": completed,
        "failed": failed,
        "remaining": list(remaining),
    }


async def get_image(prompt_id: str, *, include_base64: bool = False) -> dict | list:
    """Retrieve completed image, save to output directory, return absolute path."""
    # 1. Check job status
    try:
        result = await _fetch_job_status(prompt_id)
    except (httpx.ConnectError, httpx.TimeoutException):
        return transient_error("unreachable", f"Cannot connect to ComfyUI at {COMFYUI_URL}")
    except httpx.HTTPStatusError as exc:
        return transient_error("unreachable", f"ComfyUI returned HTTP {exc.response.status_code}")

    state = result["state"]

    if state == "pending":
        return terminal_error("invalid_inputs", f"Job {prompt_id} is still pending (queued, not started)")
    if state == "running":
        return terminal_error(
            "invalid_inputs",
            f"Job {prompt_id} is still running. Call check_job with wait to poll for completion first.",
        )
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
        return terminal_error("completed_no_output", f"Job {prompt_id} completed but produced no output images")

    # 3. Sanitize filename (FR21)
    safe_filename = os.path.basename(filename)
    if not safe_filename or safe_filename in (".", ".."):
        return terminal_error("completed_no_output", f"Job {prompt_id} produced an invalid filename")

    # 4. Fetch image bytes from ComfyUI
    params = {"filename": filename, "type": "output"}
    if subfolder:
        params["subfolder"] = subfolder

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{COMFYUI_URL}/view", params=params)
            response.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException):
        return transient_error("unreachable", f"Cannot connect to ComfyUI at {COMFYUI_URL}")
    except httpx.HTTPStatusError as exc:
        return transient_error("unreachable", f"ComfyUI returned HTTP {exc.response.status_code} fetching image")

    image_bytes = response.content

    # 5. Save to date-organized output directory
    date_str = date.today().isoformat()  # YYYY-MM-DD
    date_dir = os.path.join(OUTPUT_DIR, date_str)

    try:
        os.makedirs(date_dir, exist_ok=True)
    except OSError as exc:
        return transient_error("storage_error", f"Cannot create output directory '{date_dir}': {exc}")

    output_path = os.path.join(date_dir, safe_filename)
    if os.path.exists(output_path):
        stem, ext = os.path.splitext(safe_filename)
        for counter in range(1, 1000):
            candidate = os.path.join(date_dir, f"{stem}_{counter:03d}{ext}")
            if not os.path.exists(candidate):
                output_path = candidate
                break
    try:
        with open(output_path, "wb") as f:
            f.write(image_bytes)
    except OSError as exc:
        return transient_error("storage_error", f"Cannot write image to '{output_path}': {exc}")

    abs_path = os.path.abspath(output_path)
    logger.info("Image saved: %s", abs_path)

    result = {
        "status": "success",
        "file_path": abs_path,
        "prompt_id": prompt_id,
    }

    # Generate thumbnail for inline display via data URI
    if include_base64:
        try:
            result["thumbnail_base64"] = generate_thumbnail(image_bytes)
        except Exception:
            logger.warning("Thumbnail generation failed for %s", abs_path, exc_info=True)

    return result
