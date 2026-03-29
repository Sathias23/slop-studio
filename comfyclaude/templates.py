import json
import logging
from pathlib import Path

from comfyclaude.config import TEMPLATES_DIR
from comfyclaude.errors import terminal_error

logger = logging.getLogger(__name__)


async def list_templates() -> dict:
    """List all available templates with summary metadata."""
    templates_path = Path(TEMPLATES_DIR)
    if not templates_path.is_dir():
        return {"status": "success", "templates": []}

    templates = []
    for meta_file in sorted(templates_path.glob("*.meta.json")):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            templates.append({
                "name": meta["name"],
                "model": meta["model"],
                "description": meta["description"],
                "aspect_ratios": list(meta.get("aspect_ratios", {}).keys()),
                "expected_duration": meta.get("expected_duration", "unknown"),
            })
        except (json.JSONDecodeError, KeyError, OSError) as exc:
            logger.warning("Skipping invalid template %s: %s", meta_file.name, exc)
    return {"status": "success", "templates": templates}


async def get_template(template_name: str) -> dict:
    """Get full metadata for a specific template."""
    meta_path = Path(TEMPLATES_DIR) / f"{template_name}.meta.json"
    if not meta_path.is_file():
        return terminal_error("invalid_inputs", f"Template '{template_name}' not found")

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return terminal_error("read_error", f"Failed to read template '{template_name}': {exc}")
    return {**meta, "status": "success"}
