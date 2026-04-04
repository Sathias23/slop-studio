---
title: 'Add check_next_job tool for batch polling'
type: 'feature'
created: '2026-04-05'
status: 'done'
baseline_commit: 'e575a74'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** When generating multiple images, each `check_job` call polls a single prompt_id. Since ComfyUI processes jobs sequentially, most calls return `pending` repeatedly, wasting tool calls and context window.

**Approach:** Add a `check_next_job` tool that accepts a list of prompt_ids, polls all of them each cycle, and returns all jobs that completed during the polling window — plus remaining IDs. Failed jobs are retried (up to 3 attempts) before being reported as terminal failures. The caller calls `get_image` for each completed job and `check_next_job` again for the remaining IDs.

## Boundaries & Constraints

**Always:** Reuse `_fetch_job_status` and `_format_result` internals — no duplicated ComfyUI logic. Keep the existing `check_job` tool unchanged for single-job use.

**Ask First:** Changing the polling interval or max wait defaults.

**Never:** WebSocket-based approach. Do not remove or deprecate `check_job`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| One completes quickly | 3 IDs, first finishes within wait | Return `completed` list with that job + remaining 2 IDs | N/A |
| Multiple complete same cycle | 3 IDs, two finish in same poll cycle | Return both in `completed` list + remaining 1 ID | N/A |
| All pending, timeout | 3 IDs, none finish within wait | Return status "waiting" + all 3 IDs as remaining | N/A |
| One fails (retryable) | 3 IDs, second fails on 1st attempt | Retry that ID next cycle (up to 3 attempts), keep in remaining | N/A |
| One fails (exhausted retries) | ID fails 3 times | Include in `failed` list with error, remove from remaining | N/A |
| Empty list | `[]` | Return error | terminal_error("invalid_input") |
| Single ID | 1 ID | Works identically, completed=[] or single entry, remaining=[] | N/A |
| ComfyUI unreachable | Any IDs, network down | Return transient error | transient_error("unreachable") |

</frozen-after-approval>

## Code Map

- `slop_studio/comfyui.py` -- Add `check_next_job()` function alongside existing `check_job()`
- `slop_studio/server.py` -- Register new `check_next_job` MCP tool

## Tasks & Acceptance

**Execution:**
- [x] `slop_studio/comfyui.py` -- Add `check_next_job(prompt_ids, wait)` that polls all IDs each cycle, collects all completed jobs per cycle, tracks per-ID failure counts (max 3 retries), and returns completed + failed + remaining lists
- [x] `slop_studio/server.py` -- Register `check_next_job` as an MCP tool with docstring following existing conventions

**Acceptance Criteria:**
- Given 3 queued jobs where 2 complete during the polling window, when calling `check_next_job`, then it returns both in the `completed` list with their outputs, and the 3rd ID in `remaining`
- Given 3 queued jobs where none complete within the wait period, when calling `check_next_job`, then it returns `status: "waiting"` with all IDs in `remaining`
- Given a job that fails once, when polled again in the next cycle, then it is retried (up to 3 attempts) before being moved to the `failed` list
- Given an empty prompt_ids list, when calling `check_next_job`, then it returns a terminal error

## Design Notes

Each poll cycle checks all remaining IDs via `_fetch_job_status`. Completed jobs are collected into a `completed` list. Failed jobs increment a per-ID retry counter; after 3 failures they move to `failed`. Once any job completes or exhausts retries, return immediately with all results from that cycle. If nothing resolves by timeout, return "waiting".

Track retry counts in a local dict within the function — no persistent state needed since the caller passes the full ID list each invocation (retry counts reset per call, which is fine since ComfyUI failures are usually deterministic).

Response when jobs resolve:

```json
{
  "status": "completed",
  "completed": [
    {"prompt_id": "abc-123", "outputs": {...}},
    {"prompt_id": "def-456", "outputs": {...}}
  ],
  "failed": [],
  "remaining": ["ghi-789"]
}
```

Timeout response:

```json
{
  "status": "waiting",
  "completed": [],
  "failed": [],
  "remaining": ["abc-123", "def-456", "ghi-789"]
}
```

## Verification

**Commands:**
- `python -c "from slop_studio.comfyui import check_next_job; print('import ok')"` -- expected: no ImportError
- `python -c "from slop_studio.server import mcp; print([t.name for t in mcp._tools.values()])"` -- expected: list includes "check_next_job"

## Suggested Review Order

- Entry point: batch polling function with dedup, retry tracking, wall-clock deadline
  [`comfyui.py:329`](../../slop_studio/comfyui.py#L329)

- Inner closure that checks all remaining IDs, preserves state on early network error
  [`comfyui.py:340`](../../slop_studio/comfyui.py#L340)

- Wall-clock deadline prevents overshoot from slow poll cycles
  [`comfyui.py:386`](../../slop_studio/comfyui.py#L386)

- Response builder — always returns `status: "completed"` per spec design notes
  [`comfyui.py:406`](../../slop_studio/comfyui.py#L406)

- MCP tool registration with usage docstring
  [`server.py:190`](../../slop_studio/server.py#L190)
