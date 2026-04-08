import json
import logging
from pathlib import Path

from slop_studio.config import TEMPLATES_DIR
from slop_studio.errors import terminal_error, transient_error

logger = logging.getLogger(__name__)


def _validate_template_name(name: str) -> str | None:
    """Return error message if name is invalid, None if valid."""
    if not isinstance(name, str) or not name or not name.strip():
        return "Template name cannot be empty"
    if "/" in name:
        return f"Template name cannot contain '/': {name!r}"
    if ".." in name:
        return f"Template name cannot contain '..': {name!r}"
    if name.startswith("."):
        return f"Template name cannot start with '.': {name!r}"
    return None


def _validate_metadata(metadata: dict) -> str | None:
    """Validate meta structure. Returns error message or None."""
    missing = [f for f in ("name", "model", "description") if not isinstance(metadata.get(f), str) or not metadata[f]]
    if missing:
        return f"Missing required metadata fields: {', '.join(missing)}"

    inputs = metadata.get("inputs")
    if inputs is not None:
        if not isinstance(inputs, dict):
            return "inputs must be a JSON object"
        for key, defn in inputs.items():
            if not isinstance(defn, dict):
                return f"Input '{key}' must be a JSON object"
            if not isinstance(defn.get("node_id"), str) or not defn["node_id"]:
                return f"Input '{key}' missing required 'node_id' (string)"
            if not isinstance(defn.get("field"), str) or not defn["field"]:
                return f"Input '{key}' missing required 'field' (string)"

    aspect_ratios = metadata.get("aspect_ratios")
    if aspect_ratios is not None:
        if not isinstance(aspect_ratios, dict):
            return "aspect_ratios must be a JSON object"
        for label, dims in aspect_ratios.items():
            if not isinstance(dims, dict):
                return f"Aspect ratio '{label}' must be a JSON object with width/height"
            if (
                not isinstance(dims.get("width"), int)
                or isinstance(dims.get("width"), bool)
                or not isinstance(dims.get("height"), int)
                or isinstance(dims.get("height"), bool)
            ):
                return f"Aspect ratio '{label}' requires integer width and height"

    res_nodes = metadata.get("resolution_nodes")
    if res_nodes is not None:
        if not isinstance(res_nodes, list):
            return "resolution_nodes must be a JSON array"
        for i, node in enumerate(res_nodes):
            if not isinstance(node, dict):
                return f"resolution_nodes[{i}] must be a JSON object"
            for field in ("node_id", "width_field", "height_field"):
                if not isinstance(node.get(field), str) or not node[field]:
                    return f"resolution_nodes[{i}] missing required '{field}' (string)"

    return None


async def list_templates() -> dict:
    """List all available templates with summary metadata."""
    templates_path = Path(TEMPLATES_DIR)
    if not templates_path.is_dir():
        return {"status": "success", "templates": []}

    templates = []
    for meta_file in sorted(templates_path.glob("*.meta.json")):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            templates.append(
                {
                    "name": meta["name"],
                    "model": meta["model"],
                    "description": meta["description"],
                    "aspect_ratios": list(meta.get("aspect_ratios", {}).keys()),
                    "expected_duration": meta.get("expected_duration", "unknown"),
                }
            )
        except (json.JSONDecodeError, KeyError, OSError) as exc:
            logger.warning("Skipping invalid template %s: %s", meta_file.name, exc)
    return {"status": "success", "templates": templates}


async def get_template(template_name: str) -> dict:
    """Get full metadata for a specific template."""
    name_err = _validate_template_name(template_name)
    if name_err:
        return terminal_error("invalid_inputs", name_err)
    meta_path = Path(TEMPLATES_DIR) / f"{template_name}.meta.json"
    if not meta_path.is_file():
        return terminal_error("invalid_inputs", f"Template '{template_name}' not found")

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return terminal_error("read_error", f"Failed to read template '{template_name}': {exc}")
    return {**meta, "status": "success"}


async def add_template(name: str, workflow_json: dict, metadata: dict) -> dict:
    """Add a new workflow template with validation."""
    name_err = _validate_template_name(name)
    if name_err:
        return terminal_error("invalid_inputs", name_err)

    if not isinstance(workflow_json, dict):
        return terminal_error("invalid_inputs", "workflow_json must be a JSON object")

    metadata = {**metadata, "name": name}
    meta_err = _validate_metadata(metadata)
    if meta_err:
        return terminal_error("invalid_inputs", meta_err)

    templates_path = Path(TEMPLATES_DIR)
    workflow_path = templates_path / f"{name}.json"
    meta_path = templates_path / f"{name}.meta.json"

    if workflow_path.exists() or meta_path.exists():
        return terminal_error(
            "invalid_inputs",
            f"Template '{name}' already exists. Use update_template to modify it.",
        )

    try:
        templates_path.mkdir(parents=True, exist_ok=True)
        workflow_path.write_text(json.dumps(workflow_json, indent=2), encoding="utf-8")
        meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    except OSError as exc:
        try:
            workflow_path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
        except OSError:
            pass
        return transient_error("storage_error", f"Failed to write template '{name}': {exc}")

    logger.info("Template added: %s", name)
    return {"status": "success", "name": name, "message": f"Template '{name}' added"}


async def delete_template(name: str) -> dict:
    """Delete a workflow template by name."""
    name_err = _validate_template_name(name)
    if name_err:
        return terminal_error("invalid_inputs", name_err)

    templates_path = Path(TEMPLATES_DIR)
    meta_path = templates_path / f"{name}.meta.json"
    workflow_path = templates_path / f"{name}.json"

    if not meta_path.is_file():
        return terminal_error("invalid_inputs", f"Template '{name}' not found")

    try:
        meta_path.unlink()
    except OSError as exc:
        return transient_error("storage_error", f"Failed to delete template '{name}': {exc}")

    try:
        workflow_path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Template '%s' metadata deleted but workflow file could not be removed: %s", name, exc)

    logger.info("Template deleted: %s", name)
    return {"status": "success", "name": name, "message": f"Template '{name}' deleted"}


async def update_template(name: str, workflow_json: dict | None = None, metadata: dict | None = None) -> dict:
    """Update an existing workflow template."""
    name_err = _validate_template_name(name)
    if name_err:
        return terminal_error("invalid_inputs", name_err)

    meta_path = Path(TEMPLATES_DIR) / f"{name}.meta.json"
    if not meta_path.is_file():
        return terminal_error("invalid_inputs", f"Template '{name}' not found")

    if workflow_json is None and metadata is None:
        return terminal_error(
            "invalid_inputs",
            "At least one of workflow_json or metadata must be provided",
        )

    if workflow_json is not None:
        if not isinstance(workflow_json, dict):
            return terminal_error("invalid_inputs", "workflow_json must be a JSON object")
        workflow_path = Path(TEMPLATES_DIR) / f"{name}.json"
        try:
            workflow_path.write_text(json.dumps(workflow_json, indent=2), encoding="utf-8")
        except OSError as exc:
            return transient_error("storage_error", f"Failed to write workflow for '{name}': {exc}")

    if metadata is not None:
        metadata = {**metadata, "name": name}
        meta_err = _validate_metadata(metadata)
        if meta_err:
            return terminal_error("invalid_inputs", meta_err)
        try:
            meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        except OSError as exc:
            return transient_error("storage_error", f"Failed to write metadata for '{name}': {exc}")

    logger.info("Template updated: %s", name)
    return {"status": "success", "name": name, "message": f"Template '{name}' updated"}
