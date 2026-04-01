---
title: 'Sloppifier: CLIP synonym prompt tool'
type: 'feature'
created: '2026-04-01'
status: 'done'
baseline_commit: '5fdf5cd'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** slop-studio passes user prompts straight through to ComfyUI with no creative mutation. Brad's prompt synonymiser (a ComfyUI custom node using CLIP token embeddings to swap words for semantically similar alternatives) produces delightfully weird images, but it's locked inside ComfyUI and can't be used with arbitrary templates.

**Approach:** Port the CLIP-based synonym logic into slop-studio as a new `sloppify_prompt` MCP tool. The tool accepts a text prompt, `top_k` (embedding neighbour depth), and `synonym_ratio` (percentage of eligible words to replace, 0–100; default 100 = all words), then returns the sloppified prompt alongside the original. The LLM can then feed the result into `queue_prompt`.

## Boundaries & Constraints

**Always:**
- Use the same CLIP ViT-B/32 model and cosine-similarity approach as the original node
- Return both the sloppified prompt and the original in the response
- Load the CLIP model lazily on first call (not at server startup) to avoid slowing down startup for users who don't use this tool
- Keep the sloppifier logic in its own module (`slop_studio/sloppify.py`), separate from comfyui.py

**Ask First:**
- Adding `clip` / `torch` as hard dependencies to pyproject.toml (they're large) — consider making them optional with a clear error if missing
- Any changes to the `queue_prompt` interface or existing tools

**Never:**
- Modify existing tools or the prompt submission pipeline
- Require a GPU — must work on CPU (the original node already supports this)
- Download the CLIP model at import time or server startup

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | prompt="a sunset over mountains", top_k=5, synonym_ratio=100 | `{"status": "success", "sloppified_prompt": "...", "original_prompt": "a sunset over mountains", "words_replaced": N}` | N/A |
| Partial replacement | prompt="a big red dog", synonym_ratio=50, top_k=3 | ~half of eligible words randomly selected and replaced, others untouched | N/A |
| Single word | prompt="cat", top_k=10, synonym_ratio=100 | Single word replaced with a CLIP neighbour | N/A |
| Empty prompt | prompt="", top_k=5, synonym_ratio=100 | Terminal error: "Prompt is empty" | terminal_error("invalid_inputs", ...) |
| synonym_ratio=0 | prompt="a sunset", synonym_ratio=0 | No words replaced — returns original prompt unchanged | N/A |
| synonym_ratio out of range | synonym_ratio=150 or synonym_ratio=-1 | Rejected with input validation error | terminal_error("invalid_inputs", ...) |
| CLIP not installed | Any call when torch/clip missing | Terminal error explaining how to install deps | terminal_error("missing_dependency", ...) |
| top_k < 1 | top_k=0 | Clamped or rejected — use input validation | terminal_error("invalid_inputs", ...) |

</frozen-after-approval>

## Code Map

- `slop_studio/sloppify.py` -- NEW: core synonym logic (ported from docs/prompt_synonymiser.py) + lazy CLIP loading
- `slop_studio/server.py` -- register `sloppify_prompt` MCP tool
- `tests/test_sloppify.py` -- NEW: unit tests for synonym logic and edge cases
- `pyproject.toml` -- add torch + clip as optional dependencies

## Tasks & Acceptance

**Execution:**
- [x] `slop_studio/sloppify.py` -- create module: lazy CLIP loader, `synonymise_word()`, and `sloppify_prompt()` async function ported from the original node logic
- [x] `slop_studio/server.py` -- register `sloppify_prompt` tool with descriptive docstring
- [x] `pyproject.toml` -- add optional dependency group `[sloppify]` with `torch` and `clip` (or `git+https://github.com/openai/CLIP.git`)
- [x] `tests/test_sloppify.py` -- unit tests covering happy path, partial replacement, empty prompt, missing dependency, and top_k validation

**Acceptance Criteria:**
- Given a valid prompt, when `sloppify_prompt` is called, then a modified prompt is returned with the specified percentage of eligible words replaced by CLIP-similar tokens
- Given CLIP/torch is not installed, when `sloppify_prompt` is called, then a clear terminal error with install instructions is returned
- Given an empty prompt, when `sloppify_prompt` is called, then a terminal error is returned
- Given synonym_ratio=100, when called, then all eligible words (length > 2, alphabetic) are replaced
- Given synonym_ratio=0, when called, then the original prompt is returned unchanged
- Given synonym_ratio outside 0–100, when called, then a terminal error is returned

## Verification

**Commands:**
- `python -m pytest tests/test_sloppify.py -v` -- expected: all tests pass
- `python -m pytest tests/test_server.py -v` -- expected: existing tests still pass (no regressions)

## Suggested Review Order

**Core sloppifier logic**

- Entry point: CLIP lazy loader and synonym engine ported from Brad's ComfyUI node
  [`sloppify.py:1`](../../slop_studio/sloppify.py#L1)

- Word extraction with length/alpha filter, word-boundary-safe replacement
  [`sloppify.py:41`](../../slop_studio/sloppify.py#L41)

- CLIP cosine similarity lookup and random neighbour selection
  [`sloppify.py:47`](../../slop_studio/sloppify.py#L47)

- Main async entry: validation, ratio math, replacement loop
  [`sloppify.py:66`](../../slop_studio/sloppify.py#L66)

**MCP tool registration**

- New `sloppify_prompt` tool wired into the FastMCP server
  [`server.py:124`](../../slop_studio/server.py#L124)

**Dependencies**

- Optional `[sloppify]` dependency group with torch + CLIP
  [`pyproject.toml:18`](../../pyproject.toml#L18)

**Tests**

- Validation, missing deps, mocked CLIP, substring safety, empty synonym edge case
  [`test_sloppify.py:1`](../../tests/test_sloppify.py#L1)
