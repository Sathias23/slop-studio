# Adding Bluesky Posting to Slop Studio

Research document covering the approach, API details, and implementation plan for posting generated images from slop-studio to Bluesky.

## Current State

Slop-studio is a Python async MCP server that connects Claude Code to ComfyUI for image generation. Key characteristics:

- **Language:** Python 3.11+, async-first
- **Framework:** FastMCP 3.1.1+
- **HTTP client:** httpx
- **Output:** Images saved to `output/{YYYY-MM-DD}/{filename}`
- **No existing social integrations** — output is local-only

## Bluesky / AT Protocol Overview

Bluesky uses the AT Protocol (atproto). The Python SDK is the community-maintained `atproto` package on PyPI, which provides both sync and async clients with Pydantic models for full type safety.

## Python SDK: `atproto`

### Installation

```bash
pip install atproto
```

Or add to `pyproject.toml`:

```toml
dependencies = [
    "atproto>=0.0.55",
]
```

### Authentication

Bluesky supports **app passwords** — special-purpose credentials created at `bsky.app > Settings > App Passwords`. This is the recommended approach for bots and automation (never use a real account password).

```python
from atproto import AsyncClient

client = AsyncClient()
await client.login("handle.bsky.social", "xxxx-xxxx-xxxx-xxxx")
```

The client handles JWT token refresh automatically.

### Uploading Image Blobs

```python
image_data = Path("output/2026-03-30/image.png").read_bytes()
upload = await client.upload_blob(image_data)
# upload.blob is the BlobRef to embed in a post
```

### Creating a Post with an Image

**Simple (single image):**

```python
await client.send_image(
    text="Check out this generation!",
    image=image_data,
    image_alt="Description of the image",
)
```

**Manual (more control, multiple images):**

```python
from atproto import models

upload = await client.upload_blob(image_data)

images = [
    models.AppBskyEmbedImages.Image(
        alt="Description of the image",
        image=upload.blob,
    )
]
embed = models.AppBskyEmbedImages.Main(images=images)

post = await client.send_post(text="Check out this generation!", embed=embed)
# post.uri contains the post URI
```

### Constraints

| Constraint | Value |
|---|---|
| Max blob size | **1 MB** per image |
| Max images per post | 4 |
| Supported formats | JPEG, PNG, WebP, GIF (static only) |
| Alt text | Required field (can be empty string, but should be meaningful) |
| Post rate limit | ~35 per 5 min, ~1,667 per day |
| Blob upload rate limit | ~35 per 5 min, ~1,667 per day |
| Session creation limit | 30 per 5 min, 300 per day |
| Max post text length | 300 characters (graphemes) |

Rate limit headers (`RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset`) are returned in responses.

## Implementation Plan

### 1. New Configuration (Environment Variables)

Add to `slop_studio/config.py`:

```python
BSKY_HANDLE = os.environ.get("BSKY_HANDLE", "")
BSKY_APP_PASSWORD = os.environ.get("BSKY_APP_PASSWORD", "")
```

### 2. New Module: `slop_studio/bluesky.py`

Core implementation:

```python
"""Bluesky posting integration for slop-studio."""

from pathlib import Path

from atproto import AsyncClient, models

from slop_studio.config import BSKY_APP_PASSWORD, BSKY_HANDLE


async def _get_client() -> AsyncClient:
    """Create and authenticate a Bluesky client."""
    if not BSKY_HANDLE or not BSKY_APP_PASSWORD:
        raise ValueError(
            "BSKY_HANDLE and BSKY_APP_PASSWORD environment variables are required. "
            "Create an app password at bsky.app > Settings > App Passwords."
        )
    client = AsyncClient()
    await client.login(BSKY_HANDLE, BSKY_APP_PASSWORD)
    return client


async def post_image(
    image_path: str,
    text: str,
    alt_text: str = "",
) -> str:
    """Upload an image and post it to Bluesky. Returns the post URI."""
    client = await _get_client()

    image_data = Path(image_path).read_bytes()

    # Compress if over 1MB limit
    if len(image_data) > 1_000_000:
        image_data = _compress_image(image_data)

    upload = await client.upload_blob(image_data)

    images = [
        models.AppBskyEmbedImages.Image(
            alt=alt_text,
            image=upload.blob,
        )
    ]
    embed = models.AppBskyEmbedImages.Main(images=images)

    post = await client.send_post(text=text, embed=embed)
    return post.uri


def _compress_image(image_data: bytes) -> bytes:
    """Compress image to fit within Bluesky's 1MB limit.

    Requires Pillow: pip install Pillow
    """
    from io import BytesIO
    from PIL import Image

    img = Image.open(BytesIO(image_data))
    img.thumbnail((2048, 2048))

    buffer = BytesIO()
    img.save(buffer, format="JPEG", quality=80)
    return buffer.getvalue()
```

### 3. New MCP Tool: `post_to_bluesky`

Register a new tool in the MCP server:

```python
@mcp.tool()
async def post_to_bluesky(
    image_path: str,
    text: str,
    alt_text: str = "",
) -> str:
    """Post a generated image to Bluesky.

    Args:
        image_path: Path to the image file (from get_image output).
        text: Post text (max 300 characters).
        alt_text: Image description for accessibility.

    Returns:
        The Bluesky post URI.
    """
    from slop_studio.bluesky import post_image

    uri = await post_image(image_path, text, alt_text)
    return f"Posted to Bluesky: {uri}"
```

### 4. Dependencies to Add

In `pyproject.toml`:

```toml
dependencies = [
    "fastmcp>=3.1.1",
    "httpx>=0.28.1",
    "atproto>=0.0.55",
    "Pillow>=10.0.0",  # for image compression when needed
]
```

### 5. Error Handling

Leverage the existing error taxonomy:

- **`transient_error()`** — network failures, rate limits (retryable)
- **`terminal_error()`** — missing credentials, invalid image format (non-retryable)

### 6. Typical User Flow

```
User: /generate a sunset over mountains
Claude: [queues prompt, polls, retrieves image]
        Image saved to output/2026-03-30/sunset_abc123.png

User: Post that to Bluesky with alt text "AI-generated sunset over mountains"
Claude: [calls post_to_bluesky tool]
        Posted to Bluesky: at://did:plc:xxx/app.bsky.feed.post/yyy
```

## Files to Create/Modify

| File | Action |
|---|---|
| `slop_studio/bluesky.py` | **Create** — Bluesky client and posting logic |
| `slop_studio/config.py` | **Modify** — Add `BSKY_HANDLE`, `BSKY_APP_PASSWORD` |
| `slop_studio/server.py` (or wherever tools are registered) | **Modify** — Register `post_to_bluesky` tool |
| `pyproject.toml` | **Modify** — Add `atproto` and `Pillow` dependencies |
| `tests/test_bluesky.py` | **Create** — Unit tests with mocked API calls |

## Security Considerations

- **Never commit credentials.** Use environment variables only.
- **App passwords** are scoped and revocable — safer than account passwords.
- **Rate limiting** — the tool should surface rate limit errors clearly so Claude can advise the user to wait.
- **Image content** — Bluesky has content policies. Consider adding a note in tool output about community guidelines.

## Open Questions

1. **Session caching** — Should we keep a persistent client session across multiple posts, or create a new session per post? Creating per-post is simpler but counts against the 30/5min session limit.
2. **Thread support** — Should there be a `reply_to` parameter for posting in threads?
3. **Rich text** — `send_post()` auto-detects mentions and URLs. Do we need manual facet control?
4. **Post deletion** — Should we add a `delete_bluesky_post` tool?
5. **Image compression strategy** — Should Pillow be a hard dependency or optional, falling back to an error if the image is too large?
