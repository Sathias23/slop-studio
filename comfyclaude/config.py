import os
from pathlib import Path


def _env_or_default(key: str, default: str) -> str:
    """Return env var value, falling back to default if unset or empty."""
    value = os.environ.get(key, "")
    return value if value else default


_PACKAGE_DIR = Path(__file__).resolve().parent

COMFYUI_URL = _env_or_default("COMFYUI_URL", "http://localhost:8188").rstrip("/")

if not COMFYUI_URL.startswith(("http://", "https://")):
    raise ValueError(
        f"COMFYUI_URL must start with http:// or https://, got: {COMFYUI_URL!r}"
    )

TEMPLATES_DIR = _env_or_default(
    "COMFYCLAUDE_TEMPLATES_DIR", str(_PACKAGE_DIR.parent / "templates")
)
OUTPUT_DIR = _env_or_default("COMFYCLAUDE_OUTPUT_DIR", "./output")
