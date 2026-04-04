---
title: 'Multi-image Bluesky posts'
type: 'feature'
created: '2026-04-04'
status: 'done'
baseline_commit: '0eae19a'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `post_to_bluesky` only accepts a single image, but Bluesky supports up to 4 images per post. Grid posts are valuable for batch generations and comparison shots.

**Approach:** Change `post_image` and the MCP tool to accept either a single image path (backward compat) or a list of image entries, each with its own path and alt text. Upload and attach all images to the embed.

## Boundaries & Constraints

**Always:** Validate max 4 images. Preserve backward compat — a single `image_path` string with a single `alt_text` string must still work identically. Each image is independently compressed if over 1 MB.

**Ask First:** Changing the MCP tool parameter names or adding new required parameters beyond what the TODO specifies.

**Never:** Batch posting (multiple posts). Changing the text/tags handling. Removing the single-image calling convention.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Single image (legacy) | `image_path="a.png", alt_text="desc"` | Posts 1 image — identical to current behavior | N/A |
| Multi-image happy path | `images=[{path, alt_text}, ...]` (2–4 items) | All images uploaded, single post with grid | N/A |
| Too many images | 5+ entries in `images` | Rejected before upload | terminal_error `validation_failed` |
| Empty images list | `images=[]` | Rejected | terminal_error `validation_failed` |
| One bad file in batch | 3 images, second doesn't exist | Rejected before any upload | terminal_error `file_not_found` naming the bad path |
| Both params provided | `image_path` and `images` both set | Rejected | terminal_error `validation_failed` |

</frozen-after-approval>

## Code Map

- `slop_studio/bluesky.py` -- Core posting logic; `post_image` signature and blob upload loop
- `slop_studio/server.py` -- MCP tool definition `post_to_bluesky`; schema and docstring
- `tests/test_bluesky.py` -- Unit tests for all error paths and happy paths

## Tasks & Acceptance

**Execution:**
- [x] `slop_studio/bluesky.py` -- Add `images: list[dict] | None` param to `post_image`, validate mutual exclusivity with `image_path`, validate count 1–4, loop blob uploads, build multi-image embed
- [x] `slop_studio/server.py` -- Add `images` param to MCP tool with updated docstring, pass through to `post_image`
- [x] `tests/test_bluesky.py` -- Add tests for multi-image happy path, >4 images, empty list, mixed param conflict, one-bad-file-in-batch; verify existing single-image tests still pass

**Acceptance Criteria:**
- Given a list of 1–4 valid image paths, when `post_to_bluesky` is called with `images`, then all images appear in the post embed
- Given a single `image_path` string (no `images`), when called, then behavior is identical to current implementation
- Given both `image_path` and `images` provided, when called, then a validation error is returned
- Given >4 images, when called, then a validation error is returned before any upload

## Verification

**Commands:**
- `uv run pytest tests/test_bluesky.py -v` -- expected: all tests pass including new multi-image cases

## Suggested Review Order

**Input normalisation & validation**

- New entry point: normalises single-image and multi-image params into uniform list
  [`bluesky.py:131`](../../slop_studio/bluesky.py#L131)

- Dict structure validation added during review — guards against missing keys and non-dict entries
  [`bluesky.py:148`](../../slop_studio/bluesky.py#L148)

**Core posting logic**

- File-read and compress loop now iterates entries instead of handling one image
  [`bluesky.py:65`](../../slop_studio/bluesky.py#L65)

- Blob upload loop builds embed_images list for multi-image embed
  [`bluesky.py:100`](../../slop_studio/bluesky.py#L100)

**MCP tool schema**

- Public tool signature updated — images param added, backward-compat preserved
  [`server.py:204`](../../slop_studio/server.py#L204)

**Tests**

- Multi-image happy path — verifies 3 blobs uploaded and post created
  [`test_bluesky.py:409`](../../tests/test_bluesky.py#L409)

- Edge case coverage: >4 images, empty list, both params, bad file, missing keys, non-dict entries
  [`test_bluesky.py:435`](../../tests/test_bluesky.py#L435)
