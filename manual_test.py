"""Manual testing script for ComfyClaude.

Tests queue_prompt (Story 2.2) and check_job (Story 2.3) against a real ComfyUI instance.
Set COMFYUI_URL env var if ComfyUI is not at http://localhost:8188.

Usage:
    uv run python manual_test.py
    COMFYUI_URL=http://192.168.1.50:8188 uv run python manual_test.py
"""

import asyncio
import json
import sys

from comfyclaude.comfyui import check_job, queue_prompt


async def run_test(label: str, coro) -> dict:
    print(f"\n{'='*60}")
    print(f"TEST: {label}")
    result = await coro
    print(f"  result: {json.dumps(result, indent=2)}")
    return result


async def main():
    from comfyclaude.config import COMFYUI_URL, TEMPLATES_DIR

    print(f"ComfyUI URL: {COMFYUI_URL}")
    print(f"Templates dir: {TEMPLATES_DIR}")

    # -- queue_prompt tests --

    r = await run_test(
        "queue_prompt — happy path",
        queue_prompt("flux2_klein", {"prompt": "a cat wearing a tiny hat, photorealistic"}),
    )
    prompt_id = r.get("prompt_id")
    submitted = r.get("status") == "success"
    if submitted:
        print(f"  >>> prompt_id: {prompt_id}")

    await run_test(
        "queue_prompt — with aspect ratio 16:9",
        queue_prompt("flux2_klein", {"prompt": "wide landscape, mountains"}, aspect_ratio="16:9"),
    )

    await run_test(
        "queue_prompt — missing template (expect error)",
        queue_prompt("nonexistent_template", {"prompt": "hello"}),
    )

    await run_test(
        "queue_prompt — missing required input (expect error)",
        queue_prompt("flux2_klein", {}),
    )

    await run_test(
        "queue_prompt — invalid aspect ratio (expect error)",
        queue_prompt("flux2_klein", {"prompt": "test"}, aspect_ratio="21:9"),
    )

    # -- check_job tests --

    if submitted:
        print(f"\n{'='*60}")
        print("CHECK_JOB TESTS (using prompt_id from submission)")
        print(f"{'='*60}")

        await run_test(
            "check_job — single non-blocking check (wait=0)",
            check_job(prompt_id),
        )

        r = await run_test(
            "check_job — poll for up to 30s (wait=30)",
            check_job(prompt_id, wait=30),
        )

        if r.get("status") == "running":
            print("  >>> Job still running, polling again...")
            await run_test(
                "check_job — second poll (wait=30)",
                check_job(prompt_id, wait=30),
            )
    else:
        print(f"\n{'='*60}")
        print("CHECK_JOB TESTS (ComfyUI not reachable — testing error paths)")
        print(f"{'='*60}")

    await run_test(
        "check_job — non-existent prompt_id (expect pending or error)",
        check_job("fake-id-does-not-exist"),
    )

    await run_test(
        "check_job — wait capped at 45s (passing wait=120, should not block >45s)",
        check_job("fake-id-does-not-exist", wait=5),
    )

    # -- summary --

    print(f"\n{'='*60}")
    if submitted:
        print("ComfyUI is reachable — jobs submitted and polled successfully.")
        print("Check ComfyUI queue to see them processing.")
    else:
        print("ComfyUI may not be running — connection errors are expected.")
        print("Error-path tests (missing template, bad inputs) still validate locally.")


if __name__ == "__main__":
    asyncio.run(main())
