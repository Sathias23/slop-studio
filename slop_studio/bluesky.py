"""Bluesky posting integration for slop-studio."""

import io
import logging
import re
from pathlib import Path

from atproto import AsyncClient, client_utils, models
from atproto_client.exceptions import (
    BadRequestError,
    InvokeTimeoutError,
    NetworkError,
    RequestException,
    UnauthorizedError,
)

from slop_studio.config import get_bsky_credentials
from slop_studio.errors import terminal_error, transient_error

logger = logging.getLogger(__name__)

BLOB_LIMIT = 1_000_000  # Bluesky 1 MB blob upload limit


MAX_IMAGES = 4  # Bluesky's per-post image limit


async def post_image(
    image_path: str | None = None,
    text: str = "",
    alt_text: str = "",
    tags: list[str] | None = None,
    images: list[dict] | None = None,
) -> dict:
    """Upload image(s) and post to Bluesky.

    Accepts either the legacy single-image params (image_path + alt_text) or a
    list of ``images`` dicts, each with ``path`` and ``alt_text`` keys.
    Providing both is an error.

    Returns a dict with status and post URI on success, or a structured error.
    """
    # --- Validate credentials ---
    bsky_handle, bsky_app_password = get_bsky_credentials()
    if not bsky_handle or not bsky_app_password:
        return terminal_error(
            "missing_config",
            "Bluesky credentials not configured. Run: slop-studio auth",
        )

    # --- Normalise image entries ---
    entries = _normalise_image_entries(image_path, alt_text, images)
    if isinstance(entries, dict):
        return entries  # validation error

    # --- Build rich text with hashtag facets ---
    tb = _build_post_text(text, tags)
    full_text = tb.build_text()
    if len(full_text) > 300:
        return terminal_error(
            "validation_failed",
            f"Post text with hashtags is {len(full_text)} characters, max 300. Shorten the text or reduce tags.",
        )

    # --- Validate all files exist and read bytes ---
    image_payloads: list[tuple[bytes, str]] = []
    for entry in entries:
        path = Path(entry["path"])
        if not path.is_file():
            return terminal_error("file_not_found", f"Image file not found: {entry['path']}")
        try:
            data = path.read_bytes()
        except OSError as e:
            return terminal_error("file_not_found", f"Cannot read image: {e}")
        if len(data) > BLOB_LIMIT:
            data = _compress_image(data)
            if data is None:
                return terminal_error(
                    "compression_failed",
                    f"Image {entry['path']} is too large and could not be compressed "
                    f"under {BLOB_LIMIT} bytes even at minimum JPEG quality.",
                )
        image_payloads.append((data, entry["alt_text"]))

    # --- Authenticate ---
    client = AsyncClient()
    try:
        await client.login(bsky_handle, bsky_app_password)
    except UnauthorizedError:
        return terminal_error(
            "auth_failed",
            "Bluesky authentication failed — check BSKY_HANDLE and BSKY_APP_PASSWORD.",
        )
    except (NetworkError, InvokeTimeoutError, RequestException) as e:
        return transient_error("network_error", f"Cannot reach Bluesky: {str(e)[:200]}")

    # --- Upload blobs ---
    embed_images = []
    for data, entry_alt in image_payloads:
        try:
            uploaded = await client.upload_blob(data)
        except BadRequestError as e:
            return terminal_error("blob_upload_failed", f"Image upload rejected: {str(e)[:200]}")
        except (NetworkError, InvokeTimeoutError, RequestException) as e:
            return transient_error("network_error", f"Image upload failed: {str(e)[:200]}")
        embed_images.append(models.AppBskyEmbedImages.Image(alt=entry_alt, image=uploaded.blob))

    # --- Build embed and post ---
    embed = models.AppBskyEmbedImages.Main(images=embed_images)

    try:
        post = await client.send_post(tb, embed=embed)
    except (NetworkError, InvokeTimeoutError, RequestException) as e:
        return transient_error("network_error", f"Post failed: {str(e)[:200]}")
    except BadRequestError as e:
        return terminal_error("invalid_request", f"Post rejected: {str(e)[:200]}")

    return {"status": "success", "uri": post.uri, "cid": post.cid}


def _normalise_image_entries(
    image_path: str | None,
    alt_text: str,
    images: list[dict] | None,
) -> list[dict] | dict:
    """Return a uniform list of {path, alt_text} dicts, or an error dict."""
    if image_path and images:
        return terminal_error(
            "validation_failed",
            "Provide either image_path or images, not both.",
        )
    if images is not None:
        if not images:
            return terminal_error("validation_failed", "images list must not be empty.")
        if len(images) > MAX_IMAGES:
            return terminal_error(
                "validation_failed",
                f"Bluesky supports at most {MAX_IMAGES} images per post, got {len(images)}.",
            )
        for i, entry in enumerate(images):
            if not isinstance(entry, dict) or "path" not in entry or "alt_text" not in entry:
                return terminal_error(
                    "validation_failed",
                    f"images[{i}] must be a dict with 'path' and 'alt_text' keys.",
                )
        return images
    if image_path:
        return [{"path": image_path, "alt_text": alt_text}]
    return terminal_error(
        "validation_failed",
        "Provide either image_path or images.",
    )


# URL detection — matches three shapes that Bluesky should linkify:
#   1. scheme-prefixed:   https://example.com/path  or  http://...
#   2. www-prefixed:      www.example.com/path
#   3. bare domain+path:  github.com/foo  (requires a path so we don't match
#                         every "word.com" in prose; TLD allowlist kept narrow
#                         to avoid false positives on filenames like README.md)
_COMMON_TLDS = (
    "com",
    "org",
    "net",
    "io",
    "dev",
    "ai",
    "co",
    "app",
    "me",
    "xyz",
    "social",
    "tv",
    "gg",
    "so",
    "blog",
)
_URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s]+"
    r"|"
    r"(?:[a-zA-Z0-9](?:[-a-zA-Z0-9]{0,61}[a-zA-Z0-9])?\.)+"
    r"(?:" + "|".join(_COMMON_TLDS) + r")"
    r"/[^\s]+",
    re.IGNORECASE,
)
# Trailing punctuation that's almost always sentence punctuation, not part
# of the URL. Keep this list conservative — stripping too aggressively would
# mutilate legitimate query strings.
_TRAILING_PUNCT = ".,;:!?)"


def _split_trailing_punct(url: str) -> tuple[str, str]:
    """Peel any trailing sentence punctuation off a candidate URL match."""
    end = len(url)
    while end > 0 and url[end - 1] in _TRAILING_PUNCT:
        end -= 1
    return url[:end], url[end:]


def _normalise_url(display: str) -> str:
    """Prepend https:// to bare domains so the facet points somewhere real."""
    if display.lower().startswith(("http://", "https://")):
        return display
    return f"https://{display}"


def _build_post_text(text: str, tags: list[str] | None = None) -> client_utils.TextBuilder:
    """Build rich text with URL and optional hashtag facets using TextBuilder."""
    tb = client_utils.TextBuilder()

    # Emit alternating text / link segments so URLs render as clickable
    # facets in every Bluesky client — the composer's auto-linkify only
    # runs for posts authored in the official app.
    cursor = 0
    for match in _URL_RE.finditer(text):
        if match.start() > cursor:
            tb.text(text[cursor : match.start()])
        display, trailing = _split_trailing_punct(match.group(0))
        tb.link(display, _normalise_url(display))
        if trailing:
            tb.text(trailing)
        cursor = match.end()
    if cursor < len(text):
        tb.text(text[cursor:])

    if tags:
        # Sanitize: strip #, drop empty/whitespace-only tags
        clean = [t.lstrip("#").strip() for t in tags]
        clean = [t for t in clean if t]
        if clean:
            tb.text("\n\n")
            for i, tag in enumerate(clean):
                if i > 0:
                    tb.text(" ")
                tb.tag(f"#{tag}", tag)
    return tb


def _compress_image(image_data: bytes) -> bytes | None:
    """Compress image to fit within Bluesky's 1 MB limit.

    Uses binary search over JPEG quality to find the highest quality that
    fits. Returns None if compression fails even at minimum quality.
    Ported from Project-Cenobite.
    """
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(image_data))
    except Exception:
        logger.warning("Cannot open image for compression", exc_info=True)
        return None

    # Convert transparency/palette modes to RGB
    if img.mode in ("RGBA", "LA", "P"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        background.paste(img, mask=img.split()[-1])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    lo, hi = 30, 95
    best_buf = None
    while lo <= hi:
        mid = (lo + hi) // 2
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=mid, optimize=True)
        if buf.tell() <= BLOB_LIMIT:
            best_buf = buf
            lo = mid + 1
        else:
            hi = mid - 1

    return best_buf.getvalue() if best_buf is not None else None
