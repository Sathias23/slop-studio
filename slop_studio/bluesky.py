"""Bluesky posting integration for slop-studio."""

import io
import logging
from pathlib import Path

from atproto import AsyncClient, client_utils, models
from atproto_client.exceptions import (
    BadRequestError,
    InvokeTimeoutError,
    NetworkError,
    RequestException,
    UnauthorizedError,
)

from slop_studio.config import BSKY_APP_PASSWORD, BSKY_HANDLE
from slop_studio.errors import terminal_error, transient_error

logger = logging.getLogger(__name__)

BLOB_LIMIT = 1_000_000  # Bluesky 1 MB blob upload limit


async def post_image(
    image_path: str,
    text: str,
    alt_text: str,
    tags: list[str] | None = None,
) -> dict:
    """Upload an image and post it to Bluesky.

    Returns a dict with status and post URI on success, or a structured error.
    """
    # --- Validate credentials ---
    if not BSKY_HANDLE or not BSKY_APP_PASSWORD:
        return terminal_error(
            "missing_config",
            "BSKY_HANDLE and BSKY_APP_PASSWORD environment variables are required. "
            "Create an app password at bsky.app > Settings > App Passwords.",
        )

    # --- Validate image file ---
    path = Path(image_path)
    if not path.is_file():
        return terminal_error("file_not_found", f"Image file not found: {image_path}")

    # --- Build rich text with hashtag facets ---
    tb = _build_post_text(text, tags)
    full_text = tb.build_text()
    if len(full_text) > 300:
        return terminal_error(
            "validation_failed",
            f"Post text with hashtags is {len(full_text)} characters, max 300. "
            "Shorten the text or reduce tags.",
        )

    # --- Read and compress image ---
    try:
        image_data = path.read_bytes()
    except OSError as e:
        return terminal_error("file_not_found", f"Cannot read image: {e}")

    if len(image_data) > BLOB_LIMIT:
        image_data = _compress_image(image_data)
        if image_data is None:
            return terminal_error(
                "compression_failed",
                f"Image is too large and could not be compressed under {BLOB_LIMIT} bytes "
                "even at minimum JPEG quality.",
            )

    # --- Authenticate ---
    client = AsyncClient()
    try:
        await client.login(BSKY_HANDLE, BSKY_APP_PASSWORD)
    except UnauthorizedError:
        return terminal_error(
            "auth_failed",
            "Bluesky authentication failed — check BSKY_HANDLE and BSKY_APP_PASSWORD.",
        )
    except (NetworkError, InvokeTimeoutError, RequestException) as e:
        return transient_error(
            "network_error", f"Cannot reach Bluesky: {str(e)[:200]}"
        )

    # --- Upload blob ---
    try:
        uploaded = await client.upload_blob(image_data)
    except BadRequestError as e:
        return terminal_error(
            "blob_upload_failed", f"Image upload rejected: {str(e)[:200]}"
        )
    except (NetworkError, InvokeTimeoutError, RequestException) as e:
        return transient_error(
            "network_error", f"Image upload failed: {str(e)[:200]}"
        )

    # --- Build embed and post ---
    embed = models.AppBskyEmbedImages.Main(
        images=[
            models.AppBskyEmbedImages.Image(
                alt=alt_text,
                image=uploaded.blob,
            )
        ]
    )

    try:
        post = await client.send_post(tb, embed=embed)
    except (NetworkError, InvokeTimeoutError, RequestException) as e:
        return transient_error("network_error", f"Post failed: {str(e)[:200]}")
    except BadRequestError as e:
        return terminal_error("invalid_request", f"Post rejected: {str(e)[:200]}")

    return {"status": "success", "uri": post.uri, "cid": post.cid}


def _build_post_text(
    text: str, tags: list[str] | None = None
) -> client_utils.TextBuilder:
    """Build rich text with optional hashtag facets using TextBuilder."""
    tb = client_utils.TextBuilder()
    tb.text(text)
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
