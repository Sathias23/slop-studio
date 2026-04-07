import logging
import os
import tomllib
from pathlib import Path


logger = logging.getLogger(__name__)


def _env_or_default(key: str, default: str) -> str:
    """Return env var value, falling back to default if unset or empty."""
    value = os.environ.get(key, "")
    return value if value else default


_PACKAGE_DIR = Path(__file__).resolve().parent

CONFIG_FILE = Path.home() / ".config" / "slop-studio" / "config.toml"
PID_FILE = Path.home() / ".config" / "slop-studio" / "comfyui.pid"


def _load_config_toml() -> dict:
    """Load ~/.config/slop-studio/config.toml, returning {} on any failure."""
    try:
        with open(CONFIG_FILE, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as e:
        logger.warning("Invalid TOML in %s: %s — using defaults", CONFIG_FILE, e)
        return {}
    except OSError as e:
        logger.warning("Cannot read %s: %s — using defaults", CONFIG_FILE, e)
        return {}


_TOML_CONFIG = _load_config_toml()


def _resolve(env_key: str, toml_key: str, default: str) -> str:
    """Resolve config value: env var → config.toml → default."""
    env_val = os.environ.get(env_key, "")
    if env_val:
        return env_val
    toml_val = _TOML_CONFIG.get(toml_key)
    if toml_val is None or toml_val == "":
        return default
    if not isinstance(toml_val, str):
        logger.warning(
            "%s in %s must be a string, got %s — using default",
            toml_key, CONFIG_FILE, type(toml_val).__name__,
        )
        return default
    if not toml_val.strip():
        logger.warning(
            "%s in %s is blank — using default", toml_key, CONFIG_FILE,
        )
        return default
    return toml_val

COMFYUI_URL = _env_or_default("COMFYUI_URL", "http://localhost:8188").rstrip("/")

if not COMFYUI_URL.startswith(("http://", "https://")):
    raise ValueError(
        f"COMFYUI_URL must start with http:// or https://, got: {COMFYUI_URL!r}"
    )

COMFYUI_START_CMD = _resolve("COMFYUI_START_CMD", "comfyui_start_cmd", "")
try:
    COMFYUI_START_TIMEOUT = int(_env_or_default("COMFYUI_START_TIMEOUT", "120"))
except ValueError:
    raise ValueError(
        "COMFYUI_START_TIMEOUT must be a whole number of seconds, "
        f"got: {os.environ.get('COMFYUI_START_TIMEOUT')!r}"
    )
try:
    COMFYUI_IDLE_TIMEOUT = int(_env_or_default("COMFYUI_IDLE_TIMEOUT", "900"))
except ValueError:
    raise ValueError(
        "COMFYUI_IDLE_TIMEOUT must be a whole number of seconds, "
        f"got: {os.environ.get('COMFYUI_IDLE_TIMEOUT')!r}"
    )
if COMFYUI_IDLE_TIMEOUT < 0:
    raise ValueError(
        "COMFYUI_IDLE_TIMEOUT must be >= 0 (0 disables idle shutdown), "
        f"got: {COMFYUI_IDLE_TIMEOUT}"
    )

TEMPLATES_DIR = _resolve(
    "SLOP_STUDIO_TEMPLATES_DIR", "templates_dir", str(_PACKAGE_DIR.parent / "templates")
)
OUTPUT_DIR = _resolve("SLOP_STUDIO_OUTPUT_DIR", "output_dir", str(Path.home() / "slop-studio" / "output"))

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
