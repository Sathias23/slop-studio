# TODO: Replace serial `check_job` with `check_next_job`

## Problem

When generating multiple images (e.g. 5), the current flow requires 5 separate `check_job` calls — one per prompt_id. Since ComfyUI processes jobs sequentially, most of these calls just return `pending` repeatedly, wasting round-trips and context window.

## Proposal

Add a `check_next_job` tool that:

1. Accepts a list of prompt_ids (or inspects the ComfyUI queue directly)
2. Waits for whichever job completes next
3. Returns that job's prompt_id and status
4. Caller then calls `get_image` for the completed job and `check_next_job` again for the remaining ids

This turns N serial poll loops into a single streaming pattern:

```
queue 5 jobs → [id1, id2, id3, id4, id5]
check_next_job(ids) → id1 completed → get_image(id1)
check_next_job(remaining) → id2 completed → get_image(id2)
...
```

## Benefits

- Fewer tool calls (no redundant pending checks)
- Simpler orchestration from the caller's perspective
- Less context window waste in conversations
