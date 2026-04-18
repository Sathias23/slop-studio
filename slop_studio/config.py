import logging
import os
import re
import tomllib
from pathlib import Path

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\$\{[^}]+\}")


def _expand_env_placeholders(value: str) -> str:
    """Expand known shell-style placeholders like ${HOME} using os.environ.

    Unresolvable placeholders (e.g. ${user_config.FOO}) are left as-is so
    _has_unresolved_placeholder can catch them.
    """

    def _replace(m: re.Match) -> str:
        name = m.group(0)[2:-1]  # strip ${ and }
        return os.environ.get(name, m.group(0))

    return _PLACEHOLDER_RE.sub(_replace, value)


def _has_unresolved_placeholder(value: str) -> bool:
    """Check if a value still contains any unresolved ${...} placeholder."""
    return bool(_PLACEHOLDER_RE.search(value))


def _env_or_default(key: str, default: str) -> str:
    """Return env var value, falling back to default if unset or empty."""
    value = os.environ.get(key, "")
    if not value:
        return default
    value = _expand_env_placeholders(value)
    if _has_unresolved_placeholder(value):
        return default
    return value


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
        env_val = _expand_env_placeholders(env_val)
        if not _has_unresolved_placeholder(env_val):
            return env_val
    toml_val = _TOML_CONFIG.get(toml_key)
    if toml_val is None or toml_val == "":
        return default
    if not isinstance(toml_val, str):
        logger.warning(
            "%s in %s must be a string, got %s — using default",
            toml_key,
            CONFIG_FILE,
            type(toml_val).__name__,
        )
        return default
    if not toml_val.strip():
        logger.warning(
            "%s in %s is blank — using default",
            toml_key,
            CONFIG_FILE,
        )
        return default
    return toml_val


COMFYUI_URL = _env_or_default("COMFYUI_URL", "http://localhost:8188").rstrip("/")

if not COMFYUI_URL.startswith(("http://", "https://")):
    raise ValueError(f"COMFYUI_URL must start with http:// or https://, got: {COMFYUI_URL!r}")

COMFYUI_START_CMD = _resolve("COMFYUI_START_CMD", "comfyui_start_cmd", "")
try:
    COMFYUI_START_TIMEOUT = int(_env_or_default("COMFYUI_START_TIMEOUT", "120"))
except ValueError as exc:
    raise ValueError(
        f"COMFYUI_START_TIMEOUT must be a whole number of seconds, got: {os.environ.get('COMFYUI_START_TIMEOUT')!r}"
    ) from exc
try:
    COMFYUI_IDLE_TIMEOUT = int(_env_or_default("COMFYUI_IDLE_TIMEOUT", "900"))
except ValueError as exc:
    raise ValueError(
        f"COMFYUI_IDLE_TIMEOUT must be a whole number of seconds, got: {os.environ.get('COMFYUI_IDLE_TIMEOUT')!r}"
    ) from exc
if COMFYUI_IDLE_TIMEOUT < 0:
    raise ValueError(f"COMFYUI_IDLE_TIMEOUT must be >= 0 (0 disables idle shutdown), got: {COMFYUI_IDLE_TIMEOUT}")

TEMPLATES_DIR = _resolve(
    "SLOP_STUDIO_TEMPLATES_DIR",
    "templates_dir",
    str(_PACKAGE_DIR / "assets" / "starter-templates"),
)
OUTPUT_DIR = _resolve("SLOP_STUDIO_OUTPUT_DIR", "output_dir", str(Path.home() / "slop-studio" / "output"))

# Comfy Cloud backend config (Story 6.5 / FR-C5). API key has its own
# getter because secrets belong in credentials.json, not config.toml.
COMFY_CLOUD_URL = _resolve("COMFY_CLOUD_URL", "comfy_cloud_url", "https://cloud.comfy.org").rstrip("/")

if not COMFY_CLOUD_URL.startswith(("http://", "https://")):
    raise ValueError(f"COMFY_CLOUD_URL must start with http:// or https://, got: {COMFY_CLOUD_URL!r}")

_DEFAULT_BACKEND_RAW = _resolve("SLOP_STUDIO_DEFAULT_BACKEND", "default_backend", "local").strip().lower()
if _DEFAULT_BACKEND_RAW in ("local", "cloud"):
    DEFAULT_BACKEND = _DEFAULT_BACKEND_RAW
else:
    logger.warning(
        "SLOP_STUDIO_DEFAULT_BACKEND must be 'local' or 'cloud', got %r — using 'local'",
        _DEFAULT_BACKEND_RAW,
    )
    DEFAULT_BACKEND = "local"


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


def get_comfy_cloud_api_key() -> str:
    """Return the Comfy Cloud API key with env → credentials.json → "" precedence.

    Mirrors ``get_bsky_credentials``'s pattern. Never logs the key itself —
    only the source it was loaded from, at DEBUG level (NFR-C3).
    """
    env_key = os.environ.get("COMFY_CLOUD_API_KEY", "").strip()
    if env_key:
        env_key = _expand_env_placeholders(env_key).strip()
        if not _has_unresolved_placeholder(env_key):
            logger.debug("comfy cloud key loaded from: env")
            return env_key

    import json

    creds_file = Path.home() / ".config" / "slop-studio" / "credentials.json"
    if creds_file.is_file():
        try:
            data = json.loads(creds_file.read_text())
        except (json.JSONDecodeError, OSError):
            logger.debug("comfy cloud key loaded from: none (credentials.json unreadable)")
            return ""
        if not isinstance(data, dict):
            logger.debug("comfy cloud key loaded from: none (credentials.json is not a JSON object)")
            return ""
        comfy_cloud = data.get("comfy_cloud")
        if isinstance(comfy_cloud, dict):
            file_key = comfy_cloud.get("api_key", "")
            if isinstance(file_key, str):
                file_key = file_key.strip()
                if file_key:
                    file_key = _expand_env_placeholders(file_key).strip()
                    if not _has_unresolved_placeholder(file_key):
                        logger.debug("comfy cloud key loaded from: credentials.json")
                        return file_key

    logger.debug("comfy cloud key loaded from: none")
    return ""
