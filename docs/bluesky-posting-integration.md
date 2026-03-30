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

## Hashtags

### How hashtags work on Bluesky

Hashtags on Bluesky are **not** plain-text conventions like on early Twitter. They are structured **rich text facets** — metadata annotations that mark a byte range in the post text as a tag. A `#aiart` in plain text without a corresponding facet is just literal characters with no tag behavior (not clickable, not searchable as a tag).

The AT Protocol defines three facet types: **mentions**, **links**, and **tags**. Each facet specifies a byte range (`byteStart`, `byteEnd`) in the post text and a feature type.

### Auto-detection

The Python `atproto` SDK's `send_post()` does **not** auto-detect hashtags from text. If you pass a plain string containing `#aiart`, no tag facet is created. You must either:

1. Use the `TextBuilder` helper to construct rich text with tag facets, or
2. Manually build facet objects and pass them via the `facets` parameter

### Using TextBuilder (recommended)

The SDK provides `TextBuilder` with a chainable API for constructing rich text:

```python
from atproto import client_utils

text = (
    client_utils.TextBuilder()
    .text("Golden hour vibes ")
    .tag("#aiart", "aiart")       # display text, tag value (without #)
    .text(" ")
    .tag("#comfyui", "comfyui")
    .text(" ")
    .tag("#generativeart", "generativeart")
)

# Pass directly to send_post — it extracts text + facets automatically
await client.send_post(text)
```

The `.tag(text, tag)` method takes two arguments:
- `text` — what appears in the post (e.g. `"#aiart"`)
- `tag` — the tag value without the `#` prefix (e.g. `"aiart"`)

The `TextBuilder` handles byte offset calculation internally, which is important because offsets are in **UTF-8 bytes**, not Python string indices (emoji and non-ASCII characters occupy multiple bytes).

### Manual facet construction

For more control, build facets directly:

```python
from atproto import models

post_text = "Sunset over mountains #aiart #comfyui"

# Calculate byte offsets (must be UTF-8 byte positions, not character indices)
text_bytes = post_text.encode("utf-8")
tag1_start = text_bytes.index(b"#aiart")
tag1_end = tag1_start + len(b"#aiart")
tag2_start = text_bytes.index(b"#comfyui")
tag2_end = tag2_start + len(b"#comfyui")

facets = [
    models.AppBskyRichtextFacet.Main(
        features=[models.AppBskyRichtextFacet.Tag(tag="aiart")],
        index=models.AppBskyRichtextFacet.ByteSlice(
            byte_start=tag1_start, byte_end=tag1_end
        ),
    ),
    models.AppBskyRichtextFacet.Main(
        features=[models.AppBskyRichtextFacet.Tag(tag="comfyui")],
        index=models.AppBskyRichtextFacet.ByteSlice(
            byte_start=tag2_start, byte_end=tag2_end
        ),
    ),
]

await client.send_post(text=post_text, facets=facets)
```

### Searchability

Hashtags with proper facets are searchable on Bluesky. Users can click a tag to see other posts with the same tag. Posts without tag facets (just literal `#text`) do **not** appear in tag searches.

### Constraints

- **No hard limit** on the number of tags per post, but you're constrained by the 300-grapheme text limit
- Tag values should be **lowercase, no spaces, no `#` prefix** in the facet feature (the `#` is part of the display text only)
- Tags are indexed for search by the Bluesky AppView

### Recommended tags for AI art

Common tags in the Bluesky AI art community:

- `#aiart` — general AI-generated art
- `#generativeart` — broader generative art
- `#comfyui` — ComfyUI-specific
- `#flux` — Flux model family
- `#stablediffusion` — Stable Diffusion
- `#aiartcommunity` — community tag

### Implementation for slop-studio

A helper to build post text with hashtags:

```python
from atproto import client_utils


def build_post_text(text: str, tags: list[str] | None = None) -> client_utils.TextBuilder:
    """Build rich post text with optional hashtag facets.

    Args:
        text: The main post text.
        tags: List of tag values (without #). e.g. ["aiart", "comfyui"]

    Returns:
        TextBuilder with proper facets for all tags.
    """
    builder = client_utils.TextBuilder().text(text)

    if tags:
        builder.text("\n\n")
        for i, tag in enumerate(tags):
            if i > 0:
                builder.text(" ")
            builder.tag(f"#{tag}", tag)

    return builder
```

Usage in the MCP tool:

```python
@mcp.tool()
async def post_to_bluesky(
    image_path: str,
    text: str,
    alt_text: str,
    tags: list[str] | None = None,
) -> str:
    """Post a generated image to Bluesky.

    Args:
        image_path: Path to the image file (from get_image output).
        text: Post text (max 300 characters including tags).
        alt_text: Image description for accessibility.
        tags: Optional hashtags (without #). e.g. ["aiart", "comfyui"]
    """
    ...
```

## Alt Text

### Why it matters

Alt text is a **required field** in the Bluesky image embed schema. While it accepts an empty string, the Bluesky community strongly values accessibility — posts with missing alt text are frequently called out, and some users filter them from their feeds entirely. For an art-focused tool like slop-studio, good alt text is both an accessibility win and a way to provide context about the generation.

### What to include for AI-generated images

AI-generated images benefit from alt text that covers two layers:

1. **Visual description** — what the image actually depicts (subject, composition, colors, style)
2. **Generation context** — that it's AI-generated, and optionally the model or prompt used

Example:

> A sunset over jagged mountain peaks with cinematic orange and purple lighting. AI-generated image using Flux 2 Klein.

### Guidelines

- **Be concise but descriptive** — aim for 1-2 sentences, under 1000 characters
- **Describe what's visually present**, not artistic intent
- **Mention it's AI-generated** — transparency builds trust with the community
- **Avoid prompt-dumping** — the raw ComfyUI prompt is not useful alt text; summarize it
- **Don't start with "Image of..."** — screen readers already announce it as an image

### Auto-generating alt text

Since Claude is already in the loop when posting, it can generate alt text from the original prompt. The proposed flow:

```
User: /generate a sunset over mountains, cinematic lighting
Claude: [generates image]

User: Post that to Bluesky
Claude: [generates alt text from the prompt context]
        Alt text: "Sunset over jagged mountain peaks with warm orange
        and purple cinematic lighting. AI-generated."
        [calls post_to_bluesky with the generated alt text]
```

The `post_to_bluesky` tool should accept explicit alt text but Claude can supply a sensible default from conversation context when the user doesn't provide one. The tool description should prompt Claude to always include alt text rather than passing an empty string.

### Enforcement in tool design

The MCP tool should encourage alt text via its description and parameter naming:

```python
@mcp.tool()
async def post_to_bluesky(
    image_path: str,
    text: str,
    alt_text: str,  # required parameter, no default
) -> str:
    """Post a generated image to Bluesky.

    Args:
        image_path: Path to the image file (from get_image output).
        text: Post text (max 300 characters).
        alt_text: Image description for accessibility. Describe what the
                  image shows and note that it is AI-generated.
    """
    ...
```

Making `alt_text` a required parameter (no default) means Claude must always provide a value, which it can derive from the generation prompt.

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
