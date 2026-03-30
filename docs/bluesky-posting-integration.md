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

## Post Threading

Bluesky threads work via a `reply` reference on each post containing two **StrongRef** pointers:

- **`root`** — always points to the first post in the thread (never changes)
- **`parent`** — points to the immediate post being replied to (walks forward)

### StrongRef

A `StrongRef` uniquely identifies a record using two fields:

| Field | Description |
|---|---|
| `uri` | AT-URI, e.g. `at://did:plc:abc123/app.bsky.feed.post/3k...` |
| `cid` | Content hash of the exact record version |

Both are returned when creating a post (`post.uri`, `post.cid`).

### Thread structure example

For a 4-post thread A -> B -> C -> D:

```
Post A (root):  reply_to = None
Post B:         reply_to = { root: A, parent: A }
Post C:         reply_to = { root: A, parent: B }
Post D:         reply_to = { root: A, parent: C }
```

### Python implementation

```python
from atproto import AsyncClient, models


async def send_thread(
    client: AsyncClient,
    posts: list[dict],  # [{"text": "...", "image_data": bytes | None, "alt": "..."}]
) -> list:
    """Send a sequence of posts as a Bluesky thread."""
    if not posts:
        return []

    results = []

    # First post — no reply_to
    first = await _send_single(client, posts[0])
    results.append(first)

    root_ref = models.create_strong_ref(first)
    parent = first

    for entry in posts[1:]:
        reply_ref = models.AppBskyFeedPost.ReplyRef(
            root=root_ref,
            parent=models.create_strong_ref(parent),
        )
        post = await _send_single(client, entry, reply_to=reply_ref)
        results.append(post)
        parent = post

    return results


async def _send_single(client, entry, reply_to=None):
    """Send a single post, optionally with an image and/or reply ref."""
    embed = None
    if entry.get("image_data"):
        upload = await client.upload_blob(entry["image_data"])
        images = [
            models.AppBskyEmbedImages.Image(
                alt=entry.get("alt", ""),
                image=upload.blob,
            )
        ]
        embed = models.AppBskyEmbedImages.Main(images=images)

    return await client.send_post(
        text=entry["text"],
        embed=embed,
        reply_to=reply_to,
    )
```

### Replying to an existing post

When replying to someone else's post (or a previously created post), you need to correctly carry forward the thread root:

```python
async def reply_to_existing(client: AsyncClient, post_uri: str, text: str, embed=None):
    """Reply to an existing post by URI, correctly resolving the thread root."""
    thread = await client.app.bsky.feed.get_post_thread(params={"uri": post_uri})
    target = thread.thread.post

    # If the target is itself a reply, use its root; otherwise it IS the root
    if target.record.reply:
        root_ref = target.record.reply.root
    else:
        root_ref = models.ComAtprotoRepoStrongRef.Main(
            uri=target.uri, cid=target.cid
        )

    parent_ref = models.ComAtprotoRepoStrongRef.Main(
        uri=target.uri, cid=target.cid
    )

    return await client.send_post(
        text=text,
        embed=embed,
        reply_to=models.AppBskyFeedPost.ReplyRef(root=root_ref, parent=parent_ref),
    )
```

### Threading constraints

- **No hard thread depth limit** in the protocol — you can chain replies indefinitely
- **Client display limits** — the Bluesky app shows "continue thread" after a certain depth (UI choice, not protocol)
- **Rate limits still apply** — for long threads, a small delay between posts avoids rate limiting
- **CID immutability** — posts are immutable once created; the CID is a content hash

### Proposed MCP tool additions for threading

```python
@mcp.tool()
async def post_thread_to_bluesky(
    posts: list[dict],
) -> str:
    """Post a thread of images/text to Bluesky.

    Args:
        posts: List of {"text": str, "image_path": str | None, "alt_text": str}.
               First entry becomes the thread root.

    Returns:
        URIs for all posts in the thread.
    """
    ...

@mcp.tool()
async def reply_on_bluesky(
    post_uri: str,
    text: str,
    image_path: str = "",
    alt_text: str = "",
) -> str:
    """Reply to an existing Bluesky post.

    Args:
        post_uri: AT-URI of the post to reply to.
        text: Reply text (max 300 characters).
        image_path: Optional image to attach.
        alt_text: Image description for accessibility.

    Returns:
        The reply post URI.
    """
    ...
```

### Threaded user flow

```
User: /generate a 4-panel comic strip
Claude: [generates 4 images]

User: Post those as a thread on Bluesky
Claude: [calls post_thread_to_bluesky with 4 entries]
        Thread posted:
        1. at://did:plc:xxx/app.bsky.feed.post/aaa
        2. at://did:plc:xxx/app.bsky.feed.post/bbb
        3. at://did:plc:xxx/app.bsky.feed.post/ccc
        4. at://did:plc:xxx/app.bsky.feed.post/ddd

User: Reply to the last one saying "fin."
Claude: [calls reply_on_bluesky with post_uri of post 4]
        Replied: at://did:plc:xxx/app.bsky.feed.post/eee
```

## Open Questions

1. **Session caching** — Should we keep a persistent client session across multiple posts, or create a new session per post? Creating per-post is simpler but counts against the 30/5min session limit.
2. **Rich text** — `send_post()` auto-detects mentions and URLs. Do we need manual facet control?
3. **Post deletion** — Should we add a `delete_bluesky_post` tool?
4. **Image compression strategy** — Should Pillow be a hard dependency or optional, falling back to an error if the image is too large?
