---
title: 'Add Bluesky posting MCP tool'
type: 'feature'
created: '2026-04-04'
status: 'done'
baseline_commit: '14bd437'
context: ['docs/bluesky-posting-integration.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Generated images are local-only — there's no way to share them to Bluesky from within the conversational workflow. We have proven posting logic in Project-Cenobite that should be ported.

**Approach:** Add a `post_to_bluesky` MCP tool backed by a new `slop_studio/bluesky.py` module. Port the image compression, hashtag facet building, and error handling from Project-Cenobite's `bluesky.py`, adapted to async (`atproto.AsyncClient`). Authenticate via app password env vars.

## Boundaries & Constraints

**Always:**
- Use `atproto.AsyncClient` (async-first, matching slop-studio's pattern)
- Use `TextBuilder` for hashtag facets (not raw text)
- Compress images >1MB via Pillow before upload (binary search quality like Cenobite)
- Return structured dicts using existing `transient_error`/`terminal_error` helpers
- `alt_text` is a required parameter (no default) to encourage accessibility

**Ask First:**
- Adding threading (`post_thread_to_bluesky`) — defer to a follow-up spec
- Adding content/self-labels for moderation
- Any changes to the lifespan hook (Bsky auth should NOT block startup if unconfigured)

**Never:**
- Hard-code credentials or commit `.env` files
- Block MCP server startup when Bsky env vars are missing (it's optional)
- Add draft/review workflow (that's Cenobite's pattern, not needed here)

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | Valid image path, text ≤300 chars, alt_text, optional tags | `{"status": "success", "uri": "at://..."}` | N/A |
| Missing credentials | `BSKY_HANDLE` or `BSKY_APP_PASSWORD` unset | terminal_error | `error_type: "missing_config"`, clear setup instructions |
| Auth failure | Invalid app password | terminal_error | `error_type: "auth_failed"` |
| Image >1MB | Large PNG | Auto-compress to JPEG via binary search | If still >1MB after compression: terminal_error `"compression_failed"` |
| Image not found | Non-existent path | terminal_error | `error_type: "file_not_found"` |
| Text too long | Text + hashtags >300 graphemes | terminal_error | `error_type: "validation_failed"`, show char count |
| Rate limited | 429 / RequestException from Bsky | transient_error | `error_type: "rate_limited"` |
| Network failure | Connection refused / timeout | transient_error | `error_type: "network_error"` |

</frozen-after-approval>

## Code Map

- `slop_studio/bluesky.py` -- **CREATE** — async Bsky client: auth, image upload, compression, post creation, hashtag facets
- `slop_studio/config.py` -- ADD `BSKY_HANDLE`, `BSKY_APP_PASSWORD` env vars
- `slop_studio/server.py` -- REGISTER `post_to_bluesky` tool
- `slop_studio/errors.py` -- existing error helpers (no changes needed)
- `pyproject.toml` -- ADD `atproto>=0.0.55` dependency
- `tests/test_bluesky.py` -- **CREATE** — unit tests with mocked atproto client
- `~/Agents/Project-Cenobite/cenobite-agent/tools/bluesky.py` -- SOURCE: port compression + facet logic from here

## Tasks & Acceptance

**Execution:**
- [x] `slop_studio/config.py` -- add `BSKY_HANDLE` and `BSKY_APP_PASSWORD` env var reads using `_env_or_default` with empty string defaults
- [x] `slop_studio/bluesky.py` -- create module: `_get_client()` for auth, `_compress_image()` with binary-search quality (ported from Cenobite), `_build_post_text()` using TextBuilder for hashtag facets, `post_image()` as the main async entry point returning structured dict responses
- [x] `slop_studio/server.py` -- register `post_to_bluesky` tool with `image_path`, `text`, `alt_text` (required), `tags` (optional list[str])
- [x] `pyproject.toml` -- add `atproto>=0.0.55` to dependencies
- [x] `tests/test_bluesky.py` -- test all I/O matrix scenarios: happy path, missing config, auth failure, compression, file not found, text too long, network errors

**Acceptance Criteria:**
- Given valid Bsky credentials and a generated image, when `post_to_bluesky` is called, then the image is uploaded and a post URI is returned
- Given missing `BSKY_HANDLE` or `BSKY_APP_PASSWORD`, when `post_to_bluesky` is called, then a terminal error with setup instructions is returned (server does NOT crash)
- Given an image >1MB, when posting, then it is auto-compressed to JPEG ≤1MB before upload
- Given tags `["aiart", "comfyui"]`, when posting, then proper AT Protocol tag facets are created (not raw text)
- Given the server starts without Bsky env vars configured, when any non-Bsky tool is called, then it works normally (Bsky is opt-in)

## Verification

**Commands:**
- `uv run pytest tests/test_bluesky.py -v` -- expected: all tests pass
- `uv run pytest tests/ -v` -- expected: existing tests still pass (no regressions)

## Suggested Review Order

**Core posting logic**

- Entry point — async post with validation, compression, and structured errors
  [`bluesky.py:25`](../../slop_studio/bluesky.py#L25)

- TextBuilder hashtag facets with tag sanitization (strips #, drops empties)
  [`bluesky.py:119`](../../slop_studio/bluesky.py#L119)

- Binary-search JPEG compression ported from Cenobite
  [`bluesky.py:141`](../../slop_studio/bluesky.py#L141)

**Integration**

- MCP tool registration with required alt_text, optional tags
  [`server.py:204`](../../slop_studio/server.py#L204)

- Config env vars — opt-in, empty defaults, no startup blocking
  [`config.py:24`](../../slop_studio/config.py#L24)

- New atproto dependency
  [`pyproject.toml:11`](../../pyproject.toml#L11)

**Tests**

- 23 tests covering all I/O matrix scenarios plus review patches
  [`test_bluesky.py:1`](../../tests/test_bluesky.py#L1)
