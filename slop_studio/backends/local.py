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

from slop_studio.backends.base import Backend
from slop_studio.config import COMFYUI_URL, OUTPUT_DIR, TEMPLATES_DIR, get_comfy_cloud_api_key
from slop_studio.errors import terminal_error, transient_error

logger = logging.getLogger(__name__)


def _err(reason: str, message: str) -> dict:
    """Local-backend terminal error — tags with ``backend="local"``."""
    return terminal_error(reason, message, backend="local")


def _trans(reason: str, message: str) -> dict:
    """Local-backend transient error — tags with ``backend="local"``."""
    return transient_error(reason, message, backend="local")


DEFAULT_POLL_INTERVAL = 3  # seconds between polls (FR16)
MAX_POLL_DURATION = 45  # maximum total polling time in seconds (FR16)
MAX_FAILURE_RETRIES = 3  # retry failed jobs this many times before reporting

# Comfy partner-API node classes that proxy upstream through ComfyUI's
# account-API infrastructure and therefore require extra_data.api_key_comfy_org
# on the /prompt payload. Without the key, the node itself 403s at execution
# even though /prompt accepts the job. Extend both mappings together when
# adding templates that wrap a new partner node class.
PARTNER_API_CLASS_LABELS: dict[str, str] = {
    "OpenAIGPTImage1": "OpenAI GPT Image",
    "Flux2ProImageNode": "Flux 2 Pro",
    "GeminiImage2Node": "Google Gemini Image (Nano Banana)",
    "LumaImageNode2": "Luma UNI-1 Image",
}
PARTNER_API_CLASS_TYPES = frozenset(PARTNER_API_CLASS_LABELS.keys())


def _workflow_partner_nodes(workflow: dict) -> list[str]:
    """Return sorted partner-API class_types present in the workflow.

    Empty list means no auth forwarding is required for this workflow.
    """
    seen = set()
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        if isinstance(class_type, str) and class_type in PARTNER_API_CLASS_TYPES:
            seen.add(class_type)
    return sorted(seen)


def _format_partner_nodes(class_types: list[str]) -> str:
    """Format partner class_types with human-friendly labels for error messages."""
    return ", ".join(f"{PARTNER_API_CLASS_LABELS[ct]} ({ct})" for ct in class_types)


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


def _verify_image(file_path: str) -> None:
    """Open and verify an image file. Blocking; call via asyncio.to_thread."""
    with Image.open(file_path) as img:
        img.verify()


async def _upload_image(file_path: str) -> str:
    """Upload a local image file to ComfyUI's input directory.

    Returns the ComfyUI filename for use in LoadImage nodes.
    Raises ValueError for invalid/missing files, httpx errors for upload failures.
    """
    if not os.path.isfile(file_path):
        raise ValueError(f"Image file not found: {file_path}")

    try:
        await asyncio.to_thread(_verify_image, file_path)
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

    # Read bytes off-thread — httpx's multipart reads from the file-like object
    # synchronously, so passing an open fd would block the event loop.
    image_bytes = await asyncio.to_thread(Path(file_path).read_bytes)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{COMFYUI_URL}/upload/image",
            files={"image": (upload_name, image_bytes, mime_type)},
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
    """Replace all seed/noise_seed fields with random values to prevent cache hits.

    Capped at int32 max (2**31 - 1) rather than int64 — OpenAI's
    OpenAIGPTImage1 node validates `seed` as int32 and rejects anything
    larger. 2.1B unique values is plenty of cache-collision headroom.
    """
    for node in workflow.values():
        inputs = node.get("inputs", {})
        for key in ("seed", "noise_seed"):
            if key in inputs and isinstance(inputs[key], int):
                inputs[key] = random.randint(0, 2**31 - 1)


def _inject_resolution(workflow: dict, meta: dict, aspect_ratio: str | None) -> None:
    """Map aspect ratio label to dimensions and inject into resolution nodes.

    Two injection modes are supported per ``resolution_nodes`` entry:

    - Legacy width/height mode: ``{"node_id", "width_field", "height_field"}``
      patches ``dims["width"]`` / ``dims["height"]`` into the named fields.
      Used by flux-family templates where the workflow takes integer
      pixel dimensions.
    - ``field_map`` mode: ``{"node_id", "field_map": {src_key: dest_field}}``
      patches ``dims[src_key]`` into ``node.inputs[dest_field]`` for each
      entry in the map. Used by API-node templates whose ratio input is a
      string field (e.g. Gemini's ``aspect_ratio: "3:4"``).

    A single ``resolution_nodes`` entry must pick one mode; the validator
    rejects entries that declare both ``field_map`` and
    ``width_field``/``height_field`` at ``add_template``/``update_template``
    time. The ``field_map``-precedence behaviour below is a runtime safety
    net for hand-edited templates that bypass the validator.
    """
    if aspect_ratio is None:
        return

    aspect_ratios = meta.get("aspect_ratios", {})
    if aspect_ratio not in aspect_ratios:
        logger.error("Aspect ratio '%s' not found in meta (available: %s)", aspect_ratio, list(aspect_ratios.keys()))
        return

    dims = aspect_ratios[aspect_ratio]
    for res_node in meta.get("resolution_nodes", []):
        node_id = res_node.get("node_id")
        field_map = res_node.get("field_map")
        width_field = res_node.get("width_field")
        height_field = res_node.get("height_field")
        if not node_id or (not field_map and (not width_field or not height_field)):
            logger.error("Incomplete resolution_node definition: %s", res_node)
            continue
        if node_id not in workflow:
            logger.error("Resolution node '%s' not found in workflow", node_id)
            continue
        if "inputs" not in workflow[node_id]:
            logger.error("Resolution node '%s' has no 'inputs' key in workflow", node_id)
            continue
        if field_map:
            for src_key, dest_field in field_map.items():
                if src_key not in dims:
                    logger.error(
                        "Aspect ratio '%s' missing key '%s' required by resolution_node '%s'",
                        aspect_ratio,
                        src_key,
                        node_id,
                    )
                    continue
                workflow[node_id]["inputs"][dest_field] = dims[src_key]
        else:
            if "width" not in dims or "height" not in dims:
                logger.error(
                    "Aspect ratio '%s' missing 'width'/'height' required by resolution_node '%s'",
                    aspect_ratio,
                    node_id,
                )
                continue
            workflow[node_id]["inputs"][width_field] = dims["width"]
            workflow[node_id]["inputs"][height_field] = dims["height"]


async def queue_prompt(template_name: str, inputs: dict, aspect_ratio: str | None = None) -> dict:
    """Load a template, inject inputs, randomize seeds, and submit to ComfyUI."""
    workflow_path = Path(TEMPLATES_DIR) / f"{template_name}.json"
    meta_path = Path(TEMPLATES_DIR) / f"{template_name}.meta.json"

    if not workflow_path.is_file() or not meta_path.is_file():
        return _err("invalid_inputs", f"Template '{template_name}' not found")

    try:
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return _err("invalid_inputs", f"Failed to read template '{template_name}': {exc}")

    if not isinstance(workflow, dict):
        return _err(
            "invalid_inputs",
            f"Template '{template_name}' workflow is not a JSON object",
        )

    # Validate required inputs
    meta_inputs = meta.get("inputs", {})
    for input_name, input_def in meta_inputs.items():
        if input_def.get("type") == "required" and input_name not in inputs:
            return _err(
                "invalid_inputs",
                f"Missing required input '{input_name}': {input_def.get('description', '')}",
            )

    # Validate aspect ratio
    if aspect_ratio is not None and aspect_ratio not in meta.get("aspect_ratios", {}):
        supported = list(meta.get("aspect_ratios", {}).keys())
        return _err(
            "invalid_inputs",
            f"Unsupported aspect ratio '{aspect_ratio}'. Supported: {supported}",
        )

    # Partner-API nodes (OpenAI, BFL/Flux Pro, Gemini) proxy through ComfyUI's
    # account-API infrastructure even when executed locally, so /prompt needs
    # extra_data.api_key_comfy_org or the node 403s at execution. See
    # https://docs.comfy.org/development/comfyui-server/api-key-integration.
    # Detect on the raw workflow BEFORE _inject_inputs — partner detection
    # only looks at class_type, and _inject_inputs would otherwise spend time
    # uploading images to ComfyUI only to have the auth check fail immediately.
    partner_nodes = _workflow_partner_nodes(workflow)
    api_key = ""
    if partner_nodes:
        api_key = get_comfy_cloud_api_key()
        if not api_key:
            return _err(
                "auth_failed",
                f"Template uses partner-API node(s) ({_format_partner_nodes(partner_nodes)}) "
                "which require a ComfyUI account API key, but none is configured. "
                "Run `slop-studio auth --comfy-cloud` to set one, or add a "
                "'comfy_cloud': {'api_key': '...'} entry to "
                "~/.config/slop-studio/credentials.json. Get a key at "
                "https://platform.comfy.org/profile/api-keys.",
            )

    # Prepare workflow
    prepared = copy.deepcopy(workflow)
    try:
        await _inject_inputs(prepared, meta_inputs, inputs)
    except ValueError as exc:
        return _err("validation", str(exc))
    except httpx.TransportError:
        return _trans(
            "unreachable",
            f"Cannot upload image to ComfyUI at {COMFYUI_URL}",
        )
    except httpx.HTTPStatusError as exc:
        return _trans(
            "unreachable",
            f"ComfyUI image upload returned HTTP {exc.response.status_code}",
        )
    _randomize_seeds(prepared)
    _inject_resolution(prepared, meta, aspect_ratio)

    payload: dict = {"prompt": prepared}
    if partner_nodes:
        payload["extra_data"] = {"api_key_comfy_org": api_key}

    # Submit to ComfyUI
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{COMFYUI_URL}/prompt",
                json=payload,
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code >= 500:
            return _trans(
                "unreachable",
                f"ComfyUI returned {exc.response.status_code} at {COMFYUI_URL}",
            )
        error_body = exc.response.text
        return _err(
            "invalid_workflow",
            f"ComfyUI rejected the workflow: {error_body[:500]}",
        )
    except httpx.TransportError:
        return _trans("unreachable", f"Cannot connect to ComfyUI at {COMFYUI_URL}")

    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError):
        return _err("invalid_workflow", "ComfyUI returned a non-JSON response")

    prompt_id = data.get("prompt_id")
    if prompt_id is None:
        return _err(
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
        return _err("generation_failed", error_msg)

    # pending or running
    return {"status": state, "prompt_id": prompt_id}


async def check_job(prompt_id: str, wait: int = 0) -> dict:
    """Check job status, optionally polling until completion or timeout."""
    effective_wait = min(wait, MAX_POLL_DURATION)  # Cap at 45s

    try:
        result = await _fetch_job_status(prompt_id)
    except httpx.HTTPStatusError as exc:
        return _trans("unreachable", f"ComfyUI returned HTTP {exc.response.status_code}")
    except httpx.TransportError:
        return _trans("unreachable", f"Cannot connect to ComfyUI at {COMFYUI_URL}")

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
        except httpx.HTTPStatusError as exc:
            return _trans("unreachable", f"ComfyUI returned HTTP {exc.response.status_code}")
        except httpx.TransportError:
            return _trans("unreachable", f"Cannot connect to ComfyUI at {COMFYUI_URL}")

        if result["state"] in ("completed", "failed"):
            return _format_result(prompt_id, result)

    # Timeout reached, still running/pending
    return _format_result(prompt_id, result)


async def check_next_job(prompt_ids: list[str], wait: int = 0) -> dict:
    """Poll multiple jobs, return all that complete/fail within the wait window."""
    if not prompt_ids:
        return _err("invalid_inputs", "prompt_ids list is empty")

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
            except httpx.HTTPStatusError as exc:
                still_remaining.append(pid)
                return _trans("unreachable", f"ComfyUI returned HTTP {exc.response.status_code}")
            except httpx.TransportError:
                still_remaining.append(pid)
                return _trans("unreachable", f"Cannot connect to ComfyUI at {COMFYUI_URL}")

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
    except httpx.HTTPStatusError as exc:
        return _trans("unreachable", f"ComfyUI returned HTTP {exc.response.status_code}")
    except httpx.TransportError:
        return _trans("unreachable", f"Cannot connect to ComfyUI at {COMFYUI_URL}")

    state = result["state"]

    if state == "pending":
        return _err("invalid_inputs", f"Job {prompt_id} is still pending (queued, not started)")
    if state == "running":
        return _err(
            "invalid_inputs",
            f"Job {prompt_id} is still running. Call check_job with wait to poll for completion first.",
        )
    if state == "failed":
        error_msg = result.get("error", "Job failed in ComfyUI")
        return _err("generation_failed", error_msg)

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
        return _err("completed_no_output", f"Job {prompt_id} completed but produced no output images")

    # 3. Sanitize filename (FR21)
    safe_filename = os.path.basename(filename)
    if not safe_filename or safe_filename in (".", ".."):
        return _err("completed_no_output", f"Job {prompt_id} produced an invalid filename")

    # 4. Fetch image bytes from ComfyUI
    params = {"filename": filename, "type": "output"}
    if subfolder:
        params["subfolder"] = subfolder

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{COMFYUI_URL}/view", params=params)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return _trans("unreachable", f"ComfyUI returned HTTP {exc.response.status_code} fetching image")
    except httpx.TransportError:
        return _trans("unreachable", f"Cannot connect to ComfyUI at {COMFYUI_URL}")

    image_bytes = response.content

    # 5. Save to date-organized output directory
    date_str = date.today().isoformat()  # YYYY-MM-DD
    date_dir = os.path.join(OUTPUT_DIR, date_str)

    try:
        os.makedirs(date_dir, exist_ok=True)
    except OSError as exc:
        return _trans("storage_error", f"Cannot create output directory '{date_dir}': {exc}")

    output_path = os.path.join(date_dir, safe_filename)
    if os.path.exists(output_path):
        stem, ext = os.path.splitext(safe_filename)
        for counter in range(1, 1000):
            candidate = os.path.join(date_dir, f"{stem}_{counter:03d}{ext}")
            if not os.path.exists(candidate):
                output_path = candidate
                break
    try:
        await asyncio.to_thread(Path(output_path).write_bytes, image_bytes)
    except OSError as exc:
        return _trans("storage_error", f"Cannot write image to '{output_path}': {exc}")

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


class LocalBackend(Backend):
    """Backend that talks to a local ComfyUI instance over HTTP.

    Introduced alongside the module-level orchestration functions; Story 6.2
    will rewire the tool handlers to route through this class. Each method is
    a thin wrapper around the existing primitives — no new behavior.
    """

    name = "local"

    async def submit(self, workflow: dict) -> dict:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{COMFYUI_URL}/prompt",
                    json={"prompt": workflow},
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                return _trans(
                    "unreachable",
                    f"ComfyUI returned {exc.response.status_code} at {COMFYUI_URL}",
                )
            error_body = exc.response.text
            return _err(
                "invalid_workflow",
                f"ComfyUI rejected the workflow: {error_body[:500]}",
            )
        except httpx.TransportError:
            return _trans("unreachable", f"Cannot connect to ComfyUI at {COMFYUI_URL}")

        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError):
            return _err("invalid_workflow", "ComfyUI returned a non-JSON response")

        prompt_id = data.get("prompt_id")
        if prompt_id is None:
            return _err(
                "invalid_workflow",
                f"ComfyUI response missing prompt_id: {str(data)[:200]}",
            )
        return {"status": "success", "prompt_id": prompt_id}

    async def status(self, prompt_id: str) -> dict:
        return await _fetch_job_status(prompt_id)

    async def history(self, prompt_id: str) -> dict:
        result = await _fetch_job_status(prompt_id)
        if result.get("state") == "completed":
            return result.get("outputs", {})
        return {}

    async def view(self, filename: str, subfolder: str = "", file_type: str = "output") -> bytes:
        # Strip path components before forwarding to ComfyUI — mirrors get_image's
        # sanitization (FR21). Prevents a malicious filename with embedded path
        # traversal from reaching ComfyUI's filesystem.
        safe_filename = Path(filename).name
        if not safe_filename or safe_filename in (".", ".."):
            raise ValueError(f"Invalid filename: {filename!r}")
        params: dict[str, str] = {"filename": safe_filename, "type": file_type}
        if subfolder:
            params["subfolder"] = subfolder
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{COMFYUI_URL}/view", params=params)
            response.raise_for_status()
        return response.content

    async def upload_asset(self, file_path: str) -> str:
        return await _upload_image(file_path)
