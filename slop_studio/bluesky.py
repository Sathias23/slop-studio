"""Bluesky posting integration for slop-studio."""

import asyncio
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

from slop_studio.config import OUTPUT_DIR, get_bsky_credentials
from slop_studio.errors import terminal_error, transient_error

logger = logging.getLogger(__name__)

BLOB_LIMIT = 1_000_000  # Bluesky 1 MB blob upload limit


MAX_IMAGES = 4  # Bluesky's per-post image limit

# Cap on posts in a single self-thread. No protocol limit exists, but a large
# thread burns through the post rate limit (~35 / 5 min) and a runaway list is
# almost always a caller mistake — so we refuse early rather than half-publish.
MAX_THREAD_POSTS = 25

MAX_POST_LENGTH = 300  # Bluesky's per-post grapheme limit

# Mirrors open_gallery's allowlist — this tool publishes to a public network,
# so it gets the same (and stricter) confinement than the local viewer.
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}


def _verify_image(p: Path) -> None:
    """Open and verify an image file. Blocking; call via asyncio.to_thread."""
    from PIL import Image

    with Image.open(p) as img:
        img.verify()


async def _validate_image_path(raw_path: str) -> tuple[Path, None] | tuple[None, dict]:
    """Confine a post image to the output directory; verify it really is an image.

    Posting publishes the file's bytes to a public network, so unlike local
    file handling this refuses anything outside OUTPUT_DIR, anything without
    an image extension, and anything PIL can't parse — a prompt-injected
    path like ~/.ssh/id_rsa must not be uploadable. Returns (resolved_path,
    None) on success or (None, error_dict) on rejection.
    """
    p = Path(raw_path).resolve()
    try:
        p.relative_to(Path(OUTPUT_DIR).resolve())
    except ValueError:
        return None, terminal_error(
            "invalid_path",
            f"Image must be inside the output directory ({OUTPUT_DIR}): {raw_path}",
        )
    if p.suffix.lower() not in _IMAGE_EXTENSIONS:
        return None, terminal_error("validation_failed", f"Unsupported file type for posting: {p.suffix.lower()!r}")
    if not p.is_file():
        return None, terminal_error("file_not_found", f"Image file not found: {raw_path}")
    try:
        await asyncio.to_thread(_verify_image, p)
    except PermissionError:
        logger.debug("Cannot read image %r for verification", raw_path, exc_info=True)
        return None, terminal_error("permission_denied", f"Cannot read image: {raw_path}")
    except Exception:
        logger.debug("PIL verification failed for %r", raw_path, exc_info=True)
        return None, terminal_error("validation_failed", f"File is not a valid image: {raw_path}")
    return p, None


def _check_credentials() -> tuple[str, str] | dict:
    """Resolve Bluesky credentials, or return a missing_config error dict."""
    handle, app_password = get_bsky_credentials()
    if not handle or not app_password:
        return terminal_error(
            "missing_config",
            "Bluesky credentials not configured. Run: slop-studio auth",
        )
    return handle, app_password


async def _login(handle: str, app_password: str) -> tuple[AsyncClient, None] | tuple[None, dict]:
    """Authenticate a fresh client. Returns (client, None) or (None, error_dict)."""
    client = AsyncClient()
    try:
        await client.login(handle, app_password)
    except UnauthorizedError:
        return None, terminal_error(
            "auth_failed",
            "Bluesky authentication failed — check BSKY_HANDLE and BSKY_APP_PASSWORD.",
        )
    except (NetworkError, InvokeTimeoutError, RequestException) as e:
        return None, transient_error("network_error", f"Cannot reach Bluesky: {str(e)[:200]}")
    return client, None


async def _read_image_payloads(entries: list[dict]) -> list[tuple[bytes, str]] | dict:
    """Validate every entry (confined to OUTPUT_DIR, real image), read bytes,
    compress oversize. Returns the payload list or the first error dict."""
    image_payloads: list[tuple[bytes, str]] = []
    for entry in entries:
        path, path_err = await _validate_image_path(entry["path"])
        if path_err is not None:
            return path_err
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
    return image_payloads


async def _upload_blobs(client: AsyncClient, image_payloads: list[tuple[bytes, str]]) -> list | dict:
    """Upload each blob and return the list of embed Image models, or an error dict."""
    embed_images = []
    for data, entry_alt in image_payloads:
        try:
            uploaded = await client.upload_blob(data)
        except BadRequestError as e:
            return terminal_error("blob_upload_failed", f"Image upload rejected: {str(e)[:200]}")
        except (NetworkError, InvokeTimeoutError, RequestException) as e:
            return transient_error("network_error", f"Image upload failed: {str(e)[:200]}")
        embed_images.append(models.AppBskyEmbedImages.Image(alt=entry_alt, image=uploaded.blob))
    return embed_images


async def _send_one_post(
    client: AsyncClient,
    tb: client_utils.TextBuilder,
    image_payloads: list[tuple[bytes, str]],
    reply_to=None,
) -> dict:
    """Upload any images, build the embed, and send a single post.

    ``image_payloads`` may be empty (text-only reply). ``reply_to`` is an
    ``AppBskyFeedPost.ReplyRef`` for replies/threads, or None for a root post.
    Returns a success dict with uri/cid, or a structured error.
    """
    embed = None
    if image_payloads:
        embed_images = await _upload_blobs(client, image_payloads)
        if isinstance(embed_images, dict):
            return embed_images  # upload error
        embed = models.AppBskyEmbedImages.Main(images=embed_images)

    try:
        post = await client.send_post(tb, embed=embed, reply_to=reply_to)
    except (NetworkError, InvokeTimeoutError, RequestException) as e:
        return transient_error("network_error", f"Post failed: {str(e)[:200]}")
    except BadRequestError as e:
        return terminal_error("invalid_request", f"Post rejected: {str(e)[:200]}")

    return {"status": "success", "uri": post.uri, "cid": post.cid}


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
    creds = _check_credentials()
    if isinstance(creds, dict):
        return creds
    handle, app_password = creds

    # --- Normalise image entries ---
    entries = _normalise_image_entries(image_path, alt_text, images)
    if isinstance(entries, dict):
        return entries  # validation error

    # --- Build rich text with hashtag facets ---
    tb = _build_post_text(text, tags)
    full_text = tb.build_text()
    if len(full_text) > MAX_POST_LENGTH:
        return terminal_error(
            "validation_failed",
            f"Post text with hashtags is {len(full_text)} characters, max {MAX_POST_LENGTH}. "
            "Shorten the text or reduce tags.",
        )

    # --- Validate all files (confined to OUTPUT_DIR, real images) and read bytes ---
    image_payloads = await _read_image_payloads(entries)
    if isinstance(image_payloads, dict):
        return image_payloads

    # --- Authenticate and post ---
    client, login_err = await _login(handle, app_password)
    if login_err is not None:
        return login_err

    return await _send_one_post(client, tb, image_payloads)


async def _resolve_reply_ref(client: AsyncClient, post_uri: str) -> tuple[object, None] | tuple[None, dict]:
    """Resolve the ReplyRef (root + parent StrongRefs) for replying to ``post_uri``.

    Fetches the target post so we can carry its thread root forward: replying to
    a mid-thread post must reuse that thread's existing root, while replying to a
    top-level post makes that post both root and parent. Returns
    (ReplyRef, None) or (None, error_dict).
    """
    try:
        thread = await client.get_post_thread(post_uri, depth=0, parent_height=0)
    except BadRequestError as e:
        return None, terminal_error(
            "not_found",
            f"Could not resolve the post to reply to ({post_uri}): {str(e)[:200]}",
        )
    except (NetworkError, InvokeTimeoutError, RequestException) as e:
        return None, transient_error(
            "network_error",
            f"Cannot reach Bluesky to resolve reply target: {str(e)[:200]}",
        )

    # thread.thread is a union: ThreadViewPost (has .post), NotFoundPost, or
    # BlockedPost (neither has a usable .post). Only the first is repliable.
    post = getattr(thread.thread, "post", None)
    if post is None:
        return None, terminal_error(
            "not_found",
            f"Post not found, blocked, or inaccessible: {post_uri}",
        )

    parent_ref = models.create_strong_ref(post)
    record_reply = getattr(getattr(post, "record", None), "reply", None)
    root_ref = record_reply.root if record_reply is not None else parent_ref
    return models.AppBskyFeedPost.ReplyRef(root=root_ref, parent=parent_ref), None


async def post_reply(
    post_uri: str,
    text: str = "",
    alt_text: str = "",
    tags: list[str] | None = None,
    images: list[dict] | None = None,
    image_path: str | None = None,
) -> dict:
    """Reply to an existing Bluesky post, optionally attaching image(s).

    ``post_uri`` is the at:// URI of the post being replied to (e.g. the ``uri``
    returned by a previous post). Images are optional for a reply — a text-only
    reply is allowed. Returns a dict with the new post's uri/cid, or an error.
    """
    creds = _check_credentials()
    if isinstance(creds, dict):
        return creds
    handle, app_password = creds

    if not post_uri or not isinstance(post_uri, str):
        return terminal_error(
            "validation_failed",
            "post_uri is required — the at:// URI of the post to reply to.",
        )

    # Images are optional for a reply; only normalise when supplied.
    image_payloads: list[tuple[bytes, str]] = []
    if image_path or images:
        entries = _normalise_image_entries(image_path, alt_text, images)
        if isinstance(entries, dict):
            return entries
    else:
        entries = []

    tb = _build_post_text(text, tags)
    full_text = tb.build_text()
    if not full_text.strip() and not entries:
        return terminal_error("validation_failed", "A reply must contain text, image(s), or both.")
    if len(full_text) > MAX_POST_LENGTH:
        return terminal_error(
            "validation_failed",
            f"Reply text with hashtags is {len(full_text)} characters, max {MAX_POST_LENGTH}. "
            "Shorten the text or reduce tags.",
        )

    if entries:
        image_payloads = await _read_image_payloads(entries)
        if isinstance(image_payloads, dict):
            return image_payloads

    client, login_err = await _login(handle, app_password)
    if login_err is not None:
        return login_err

    reply_ref, ref_err = await _resolve_reply_ref(client, post_uri)
    if ref_err is not None:
        return ref_err

    return await _send_one_post(client, tb, image_payloads, reply_to=reply_ref)


async def _prepare_thread_entry(entry: dict) -> tuple[client_utils.TextBuilder, list[tuple[bytes, str]]] | dict:
    """Validate one thread entry and read its image bytes, without any network call.

    Returns (TextBuilder, image_payloads) on success or an error dict. Doing all
    of this up front lets ``post_thread`` reject a bad entry before it publishes
    any post, so a typo in post 3 never leaves a half-finished thread live.
    """
    if not isinstance(entry, dict):
        return terminal_error("validation_failed", "each post must be a dict with at least a 'text' key.")

    image_path = entry.get("image_path")
    images = entry.get("images")
    alt_text = entry.get("alt_text", "")

    if image_path or images:
        entries = _normalise_image_entries(image_path, alt_text, images)
        if isinstance(entries, dict):
            return entries
    else:
        entries = []

    tb = _build_post_text(entry.get("text", ""), entry.get("tags"))
    full_text = tb.build_text()
    if not full_text.strip() and not entries:
        return terminal_error("validation_failed", "each post must contain text, image(s), or both.")
    if len(full_text) > MAX_POST_LENGTH:
        return terminal_error(
            "validation_failed",
            f"post text with hashtags is {len(full_text)} characters, max {MAX_POST_LENGTH}.",
        )

    image_payloads: list[tuple[bytes, str]] = []
    if entries:
        image_payloads = await _read_image_payloads(entries)
        if isinstance(image_payloads, dict):
            return image_payloads
    return tb, image_payloads


async def post_thread(posts: list[dict]) -> dict:
    """Publish a sequence of posts as a single Bluesky self-thread.

    ``posts`` is an ordered list of post dicts; each accepts the same fields as a
    single post (``text``, ``tags``, ``image_path`` + ``alt_text``, or ``images``).
    The first entry becomes the thread root and every subsequent entry replies to
    the one before it, all sharing the root ref.

    Every entry is validated and its images read BEFORE anything is published, so
    a malformed entry fails the whole call cleanly. If a later post fails mid-send
    (e.g. a transient network error), the returned error includes ``posted`` — the
    uri/cid of the posts that did go live — so the caller can decide whether to
    resume or delete them.
    """
    creds = _check_credentials()
    if isinstance(creds, dict):
        return creds
    handle, app_password = creds

    if not isinstance(posts, list) or not posts:
        return terminal_error("validation_failed", "posts must be a non-empty list of post dicts.")
    if len(posts) > MAX_THREAD_POSTS:
        return terminal_error(
            "validation_failed",
            f"A thread supports at most {MAX_THREAD_POSTS} posts, got {len(posts)}.",
        )

    prepared = []
    for i, entry in enumerate(posts):
        result = await _prepare_thread_entry(entry)
        if isinstance(result, dict):
            result["error"] = f"posts[{i}]: {result['error']}"
            return result
        prepared.append(result)

    client, login_err = await _login(handle, app_password)
    if login_err is not None:
        return login_err

    posted: list[dict] = []
    root_ref = None
    parent_ref = None
    for i, (tb, image_payloads) in enumerate(prepared):
        reply_to = None
        if root_ref is not None:
            reply_to = models.AppBskyFeedPost.ReplyRef(root=root_ref, parent=parent_ref)

        res = await _send_one_post(client, tb, image_payloads, reply_to=reply_to)
        if res.get("status") != "success":
            return {
                "status": "error",
                "error_type": res.get("error_type", "thread_failed"),
                "error": f"Thread failed at post {i + 1} of {len(prepared)}: {res.get('error', '')}",
                "retry_suggested": res.get("retry_suggested", False),
                "posted": posted,
            }

        posted.append({"uri": res["uri"], "cid": res["cid"]})
        parent_ref = models.ComAtprotoRepoStrongRef.Main(uri=res["uri"], cid=res["cid"])
        if root_ref is None:
            root_ref = parent_ref

    return {
        "status": "success",
        "uri": posted[0]["uri"],
        "count": len(posted),
        "posts": posted,
    }


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
