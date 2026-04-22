"""Live end-to-end smoke for the gpt-image-2 starter templates.

Submits a real job to the local ComfyUI (127.0.0.1:8188 by default) and
polls until completion. Hits OpenAI's paid API via ComfyUI's
OpenAIGPTImage1 node — small cost per run.

Usage:
    uv run python scripts/smoke_gpt_image_2.py            # t2i smoke
    uv run python scripts/smoke_gpt_image_2.py edit PATH  # image-edit smoke
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from slop_studio.backends.router import check_next_job, get_image, route_submission


async def queue_prompt(template_name, inputs, aspect_ratio=None):
    return await route_submission(template_name, inputs, aspect_ratio)


async def smoke_t2i() -> int:
    print("=== t2i smoke ===")
    submit = await queue_prompt(
        template_name="api_openai_gpt_image_2_t2i",
        inputs={
            "prompt": "a tiny succulent in a ceramic pot on a sunlit windowsill, soft focus",
            "quality": "low",
        },
        aspect_ratio="1:1",
    )
    print("submit:", submit)
    if submit.get("status") != "success":
        return 1

    prompt_id = submit["prompt_id"]
    print(f"polling {prompt_id} (wait up to 120s)...")
    status = await check_next_job([prompt_id], wait=120)
    print("status:", status)

    completed = status.get("completed", [])
    if not completed:
        print("no completions — inspect status above")
        return 2

    result = await get_image(completed[0]["prompt_id"], include_base64=False)
    if isinstance(result, list):
        result = result[0]
    print("image:", result.get("image_path"))
    return 0


async def smoke_edit(reference_path: str) -> int:
    print("=== image-edit smoke ===")
    if not Path(reference_path).is_file():
        print(f"reference image not found: {reference_path}")
        return 1

    submit = await queue_prompt(
        template_name="api_openai_gpt_image_2_image_edit",
        inputs={
            "prompt": "convert the background to a soft pastel gradient, keep the subject sharp",
            "image1": reference_path,
            "quality": "low",
        },
        aspect_ratio="1:1",
    )
    print("submit:", submit)
    if submit.get("status") != "success":
        return 1

    prompt_id = submit["prompt_id"]
    print(f"polling {prompt_id} (wait up to 120s)...")
    status = await check_next_job([prompt_id], wait=120)
    print("status:", status)

    completed = status.get("completed", [])
    if not completed:
        print("no completions — inspect status above")
        return 2

    result = await get_image(completed[0]["prompt_id"], include_base64=False)
    if isinstance(result, list):
        result = result[0]
    print("image:", result.get("image_path"))
    return 0


async def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "edit":
        if len(argv) < 3:
            print("usage: smoke_gpt_image_2.py edit <reference-image-path>")
            return 2
        return await smoke_edit(argv[2])
    return await smoke_t2i()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv)))
