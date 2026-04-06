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

COMFYUI_START_CMD = _env_or_default("COMFYUI_START_CMD", "")
try:
    COMFYUI_START_TIMEOUT = int(_env_or_default("COMFYUI_START_TIMEOUT", "120"))
except ValueError:
    raise ValueError(
        "COMFYUI_START_TIMEOUT must be a whole number of seconds, "
        f"got: {os.environ.get('COMFYUI_START_TIMEOUT')!r}"
    )

TEMPLATES_DIR = _env_or_default(
    "SLOP_STUDIO_TEMPLATES_DIR", str(_PACKAGE_DIR.parent / "templates")
)
OUTPUT_DIR = _env_or_default("SLOP_STUDIO_OUTPUT_DIR", str(Path.home() / "slop-studio" / "output"))

def get_bsky_credentials() -> tuple[str, str]:
    """Return (handle, app_password) using 3-tier fallback.

    Precedence: env vars → ~/.config/slop-studio/credentials.json → ("", "").
    """
    handle = os.environ.get("BSKY_HANDLE", "")
    app_password = os.environ.get("BSKY_APP_PASSWORD", "")
    if handle and app_password:
        return handle, app_password

    # Fall back to central credentials file
    import json
    creds_file = Path.home() / ".config" / "slop-studio" / "credentials.json"
    if creds_file.is_file():
        try:
            data = json.loads(creds_file.read_text())
            bsky = data.get("bluesky", {})
            file_handle = bsky.get("handle", "")
            file_password = bsky.get("app_password", "")
            if file_handle and file_password:
                return file_handle, file_password
        except (json.JSONDecodeError, OSError):
            pass

    return handle, app_password
