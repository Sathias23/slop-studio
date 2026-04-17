"""probe_cloud.py — Story 6.4 pre-implementation probe spike.

Answers four deferred Cloud API unknowns that block CloudBackend implementation:

  probe-2 — LoadImage asset-reference format (which field from /api/assets
            response works as the `image` input on a LoadImage node?)
  probe-3 — httpx cross-host redirect auth-header behavior on /api/view
            (does `X-API-Key` leak to the signed bucket URL?)
  probe-4 — 429 disambiguation: does the error body's `code`/`type` field
            distinguish rate-limit from payment-lapsed?
  probe-5 — Concurrency overflow on Standard plan: do excess concurrent
            submissions queue silently or reject at submit?
  probe-real — Real-pipeline verification: submit a full API-format workflow
            JSON, poll to completion, fetch outputs, report credits + wall-time.
            Verifies the Flux inference path (UNETLoader/CLIPLoader/VAELoader/
            SamplerCustomAdvanced/Flux2Scheduler/ReferenceLatent) end-to-end
            against cloud — the passthrough workflow in probes #2/#3/#5 does not.

Each subcommand is independently runnable and prints a Markdown-ready block
for direct paste into SecA.6 of the 2026-04-14 research doc.

Budget: ~60 credits total. See spec-6-4-probe-spike.md for per-probe ceilings.

Usage:
    python scripts/probe_cloud.py probe-2 [--dry-run]
    python scripts/probe_cloud.py probe-3 [--dry-run]
    python scripts/probe_cloud.py probe-4 [--dry-run]
    python scripts/probe_cloud.py probe-5 [--dry-run]
    python scripts/probe_cloud.py probe-real --workflow <path> [--dry-run]

Exit codes:
    0 — probe resolved (finding captured)
    1 — probe completed but could not trigger / documented failure
    2 — env/auth error; nothing sent
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

CLOUD_BASE_URL = "https://cloud.comfy.org"
TIMEOUT_SECONDS = 30.0
REPO_ROOT = Path(__file__).resolve().parent.parent

# Per research doc SecA.1, submission body wraps workflow: {"prompt": <workflow>}
# (same shape as local ComfyUI's /prompt endpoint).


def _mask_key(key: str) -> str:
    """Mask an API key for safe logging. 'comfyui-abcdefg***' style."""
    if key.startswith("comfyui-") and len(key) >= 15:
        return key[:15] + "***"
    return "***"


def _load_key() -> str:
    """Load COMFY_CLOUD_API_KEY from env via .env; exit(2) if missing."""
    load_dotenv(REPO_ROOT / ".env")
    key = os.environ.get("COMFY_CLOUD_API_KEY")
    if not key:
        print(
            "ERROR: COMFY_CLOUD_API_KEY not set. Add it to .env. Aborting.",
            file=sys.stderr,
        )
        sys.exit(2)
    return key


def _client(key: str, event_hooks: dict | None = None) -> httpx.AsyncClient:
    """Async httpx client matching house style (backends/local.py: 30s timeout)."""
    return httpx.AsyncClient(
        timeout=TIMEOUT_SECONDS,
        headers={"X-API-Key": key},
        event_hooks=event_hooks or {},
    )


def _print_block(n: int, title: str, status: str, finding: str, evidence: str) -> None:
    """Print a paste-ready Markdown block for SecA.6."""
    print(f"### Probe #{n} — {title}\n")
    print(f"**Status:** {status}\n")
    print(f"**Finding:** {finding}\n")
    print("**Raw evidence:**\n")
    print("```")
    print(evidence.rstrip())
    print("```")
    print()


def _find_reference_image() -> Path | None:
    """Return the first PNG found under output/. Used as a LoadImage probe subject."""
    output_dir = REPO_ROOT / "output"
    if not output_dir.exists():
        return None
    for path in sorted(output_dir.rglob("*.png")):
        return path
    return None


# ---------- Probe 2: LoadImage asset-reference format ----------


def _minimal_loadimage_workflow(candidate: str, prefix: str = "probe") -> dict:
    """LoadImage -> SaveImage pass-through. Exercises asset resolution only,
    avoids model-loader validation (cloud rejected UnetLoaderGGUF as
    unsupported — finding from initial probe-2 run). Uses ComfyUI core
    nodes only. Near-zero credit cost (~0.4 credits per run)."""
    return {
        "1": {
            "class_type": "LoadImage",
            "inputs": {"image": candidate},
        },
        "2": {
            "class_type": "SaveImage",
            "inputs": {
                "filename_prefix": prefix,
                "images": ["1", 0],
            },
        },
    }


def _parse_json_safe(resp: httpx.Response) -> dict | None:
    """Parse resp.json() defensively. Returns None on decode failure or non-dict body."""
    try:
        body = resp.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


async def _upload_ref_asset(client: httpx.AsyncClient) -> tuple[str, dict]:
    """Upload a reference image to /api/assets; return (asset_hash, full_response_dict).

    `asset_hash` is the filename-shaped identifier that the upload response's own
    `preview_url` uses. Probe-3 confirmed at execution time that `name` alone fails
    with ImageDownloadError — the worker resolves by hash, not display name.
    Uploads are free (content-addressed).
    """
    ref = _find_reference_image()
    if ref is None:
        raise RuntimeError("no PNG in output/ — generate one first")
    with ref.open("rb") as f:
        files = {"file": (ref.name, f, "image/png")}
        resp = await client.post(f"{CLOUD_BASE_URL}/api/assets", files=files)
    resp.raise_for_status()
    body = _parse_json_safe(resp)
    if body is None:
        raise RuntimeError(f"upload response not a JSON object: {resp.text[:200]}")
    asset_hash = body.get("asset_hash")
    if not isinstance(asset_hash, str):
        raise RuntimeError(f"upload response missing `asset_hash` field: {resp.text[:200]}")
    return asset_hash, body


async def probe_2(dry_run: bool) -> int:
    """Probe #2 cost note: one minimal passthrough per successful submit. If the
    first candidate wins (asset_hash per probe-3 evidence), cost is ~0.4 credits.
    Worst case (all 6 candidates submit but fail at execute): ~2.4 credits."""
    key = _load_key()

    ref_image = _find_reference_image()
    if ref_image is None:
        print(
            "ERROR: no PNG found in output/. Generate one first, then re-run probe-2.",
            file=sys.stderr,
        )
        return 1

    if dry_run:
        print(f"[DRY RUN] Would POST {CLOUD_BASE_URL}/api/assets")
        print(f"  Headers: X-API-Key: {_mask_key(key)}")
        print(f"  File:    {ref_image.relative_to(REPO_ROOT)} ({ref_image.stat().st_size} bytes)")
        print(f"[DRY RUN] Then POST {CLOUD_BASE_URL}/api/prompt with a minimal")
        print("          LoadImage->SaveImage workflow for each candidate field from the upload")
        print("          response; first execution-success wins.")
        return 0

    async with _client(key) as client:
        # 1. Upload reference image via shared helper
        try:
            _asset_hash, asset = await _upload_ref_asset(client)
        except RuntimeError as e:
            _print_block(
                2,
                "LoadImage reference",
                "[FAIL] upload precondition failed",
                str(e),
                "Ensure output/ contains at least one PNG.",
            )
            return 1
        except httpx.HTTPStatusError as e:
            _print_block(
                2,
                "LoadImage reference",
                f"[FAIL] upload rejected ({e.response.status_code})",
                "Asset upload failed before candidate testing.",
                f"{e.response.status_code} {e.response.reason_phrase}\n{e.response.text}",
            )
            return 1
        except httpx.TransportError as e:
            _print_block(
                2,
                "LoadImage reference",
                "[FAIL] transport error",
                f"POST /api/assets failed: {type(e).__name__}",
                str(e),
            )
            return 1

        # 2. Gather candidate fields from upload response. Order by empirical
        # evidence: `asset_hash` first (upload response's own `preview_url` uses
        # it as the `/api/view?filename=` param — strong hint), then id-shaped,
        # then display-name.
        candidate_keys = ("asset_hash", "hash", "id", "asset_id", "name", "filename")
        candidates = [(k, asset[k]) for k in candidate_keys if isinstance(asset.get(k), str)]

        if not candidates:
            _print_block(
                2,
                "LoadImage reference",
                "[FAIL] no candidates in upload response",
                "Upload response contained no string-typed fields matching expected names.",
                f"Response:\n{json.dumps(asset, indent=2)}",
            )
            return 1

        # 3. Try each candidate end-to-end — submit + poll execution. Submit-only
        #    is insufficient: the validator accepts any string at LoadImage.image
        #    without verifying asset existence (discovered during initial run,
        #    where `name` passed submit but failed with ImageDownloadError at
        #    execution). First EXECUTION-success wins.
        #    Order: asset_hash first (preview_url hint), then id, then name.
        attempts = []
        for field_name, candidate in candidates:
            test_workflow = _minimal_loadimage_workflow(candidate)

            try:
                sub = await client.post(
                    f"{CLOUD_BASE_URL}/api/prompt",
                    json={"prompt": test_workflow},
                )
            except httpx.TransportError as e:
                attempts.append(f"  field={field_name!r}  ->  TRANSPORT ERROR: {type(e).__name__}: {e}")
                continue

            if not (200 <= sub.status_code < 300):
                attempts.append(f"  field={field_name!r}  ->  submit rejected ({sub.status_code}) {sub.text[:200]}")
                continue

            sub_body = _parse_json_safe(sub)
            prompt_id = sub_body.get("prompt_id") if sub_body else None
            if not isinstance(prompt_id, str) or not prompt_id:
                attempts.append(f"  field={field_name!r}  ->  2xx but prompt_id missing/non-string")
                continue

            # Poll execution (max ~30s for a trivial passthrough)
            exec_state = None
            exec_body: dict = {}
            for _ in range(10):
                await asyncio.sleep(3)
                try:
                    stat = await client.get(f"{CLOUD_BASE_URL}/api/job/{prompt_id}/status")
                    stat.raise_for_status()
                except (httpx.HTTPStatusError, httpx.TransportError):
                    break
                parsed = _parse_json_safe(stat)
                if parsed is None:
                    break
                exec_body = parsed
                exec_state = exec_body.get("status") or exec_body.get("state")
                if exec_state in ("success", "completed", "error", "failed"):
                    break

            if exec_state in ("success", "completed"):
                attempts.append(
                    f"  field={field_name!r} value={candidate!r}  ->  submit 2xx, execute {exec_state} [OK]"
                )
                evidence = (
                    f"Upload response:\n{json.dumps(asset, indent=2)}\n\n"
                    f"Submission attempts (end-to-end):\n" + "\n".join(attempts) + "\n"
                )
                _print_block(
                    2,
                    "LoadImage reference",
                    f"[OK] `{field_name}` works end-to-end",
                    f"The LoadImage node's `image` input is resolved by the cloud worker using "
                    f"the `{field_name}` value from POST /api/assets. Submit-only is insufficient "
                    f"(validator accepts any string); must verify via execution. "
                    f"CloudBackend.upload_asset() should return this field.",
                    evidence,
                )
                return 0

            err_raw = exec_body.get("error_message")
            err = str(err_raw) if err_raw else "(no error_message)"
            attempts.append(
                f"  field={field_name!r} value={candidate!r}  ->  submit 2xx, execute {exec_state}  error={err[:200]}"
            )

        evidence = (
            f"Upload response:\n{json.dumps(asset, indent=2)}\n\n"
            f"All candidates failed end-to-end:\n" + "\n".join(attempts) + "\n"
        )
        _print_block(
            2,
            "LoadImage reference",
            "[FAIL] no candidate resolved at execution",
            "None of the fields from /api/assets produced a successful execution. "
            "Manual inspection of the upload response shape required.",
            evidence,
        )
        return 1


# ---------- Probe 3: /api/view cross-host redirect auth behavior ----------


async def probe_3(dry_run: bool) -> int:
    key = _load_key()

    if dry_run:
        print(f"[DRY RUN] Would POST {CLOUD_BASE_URL}/api/assets (upload ref image, free)")
        print(f"[DRY RUN] Would POST {CLOUD_BASE_URL}/api/prompt (minimal LoadImage->SaveImage)")
        print(f"  Headers: X-API-Key: {_mask_key(key)}")
        print(f"[DRY RUN] Then poll GET {CLOUD_BASE_URL}/api/job/<id>/status until completed")
        print(f"[DRY RUN] Then GET {CLOUD_BASE_URL}/api/view?filename=<out>  follow_redirects=True")
        print("[DRY RUN] Log each request (URL + headers) via event_hooks; inspect whether")
        print("          X-API-Key is sent to the redirect target host.")
        return 0

    logged: list[dict[str, Any]] = []

    async def log_request(request: httpx.Request) -> None:
        logged.append(
            {
                "url": str(request.url),
                "host": request.url.host,
                "x_api_key_present": "x-api-key" in {h.lower() for h in request.headers},
                "authorization_present": "authorization" in {h.lower() for h in request.headers},
            }
        )

    # 1. Upload ref asset, then submit a minimal LoadImage->SaveImage workflow
    async with _client(key) as client:
        try:
            asset_hash, _asset = await _upload_ref_asset(client)
        except (RuntimeError, httpx.HTTPStatusError, httpx.TransportError) as e:
            _print_block(
                3,
                "redirect auth-stripping",
                "[FAIL] asset upload failed",
                f"{type(e).__name__}: {e}",
                "Cannot proceed without a test asset.",
            )
            return 1

        workflow = _minimal_loadimage_workflow(asset_hash, prefix="probe3")

        try:
            sub = await client.post(f"{CLOUD_BASE_URL}/api/prompt", json={"prompt": workflow})
            sub.raise_for_status()
        except (httpx.HTTPStatusError, httpx.TransportError) as e:
            status_code = getattr(getattr(e, "response", None), "status_code", "transport")
            body_text = getattr(getattr(e, "response", None), "text", str(e))[:500]
            _print_block(
                3,
                "redirect auth-stripping",
                f"[FAIL] submit failed ({status_code})",
                "Could not start a job for redirect inspection.",
                f"{status_code}\n{body_text}",
            )
            return 1

        sub_body = _parse_json_safe(sub)
        prompt_id = sub_body.get("prompt_id") if sub_body else None
        if not isinstance(prompt_id, str) or not prompt_id:
            _print_block(
                3,
                "redirect auth-stripping",
                "[FAIL] no prompt_id in submit response",
                "Submit succeeded but response did not contain a string prompt_id.",
                sub.text[:500],
            )
            return 1

        # 2. Poll status until completed or failed (max ~90s).
        #    Note: /api/job/{id}/status returns terminal state ONLY — no outputs.
        #    After success, call /api/history for the outputs dict.
        body: dict = {}
        for _ in range(30):
            await asyncio.sleep(3)
            try:
                stat = await client.get(f"{CLOUD_BASE_URL}/api/job/{prompt_id}/status")
                stat.raise_for_status()
            except (httpx.HTTPStatusError, httpx.TransportError) as e:
                status_code = getattr(getattr(e, "response", None), "status_code", "transport")
                body_text = getattr(getattr(e, "response", None), "text", str(e))[:500]
                _print_block(
                    3,
                    "redirect auth-stripping",
                    f"[FAIL] status polling failed ({status_code})",
                    "Could not determine job completion state.",
                    f"{status_code}\n{body_text}",
                )
                return 1
            parsed = _parse_json_safe(stat)
            if parsed is None:
                _print_block(
                    3,
                    "redirect auth-stripping",
                    "[FAIL] status returned non-JSON body",
                    "Cannot parse status response.",
                    stat.text[:500],
                )
                return 1
            body = parsed
            state = body.get("status") or body.get("state")
            if state in ("completed", "success"):
                break
            if state in ("failed", "error"):
                _print_block(
                    3,
                    "redirect auth-stripping",
                    "[FAIL] job failed before completion",
                    "Probe job did not produce an output to inspect.",
                    json.dumps(body, indent=2)[:1500],
                )
                return 1
        else:
            _print_block(
                3,
                "redirect auth-stripping",
                "[WARN] timed out waiting for completion",
                "Job did not complete within 90s; cannot inspect redirect.",
                f"last status body:\n{json.dumps(body, indent=2)[:1500]}",
            )
            return 1

        # 3. Fetch history for outputs. Probe-3 first run confirmed /api/history/{id}
        #    returns {"history": [{prompt_id, outputs, ...}, ...]}. /api/history_v2
        #    remains untested. Try both; first that yields outputs wins.
        outputs: dict | None = None
        history_body: Any = None
        for path in (f"/api/history/{prompt_id}", f"/api/history_v2?prompt_id={prompt_id}"):
            try:
                hist = await client.get(f"{CLOUD_BASE_URL}{path}")
                hist.raise_for_status()
            except (httpx.HTTPStatusError, httpx.TransportError):
                continue
            parsed_hist = _parse_json_safe(hist)
            if parsed_hist is None:
                continue
            history_body = parsed_hist
            # Observed cloud shape: {"history": [{prompt_id, outputs, ...}, ...]}
            # Also handle local-style {prompt_id: {outputs: ...}} as fallback.
            if isinstance(history_body.get("history"), list):
                for entry in history_body["history"]:
                    if isinstance(entry, dict) and entry.get("prompt_id") == prompt_id:
                        outputs = entry.get("outputs")
                        break
            elif prompt_id in history_body and isinstance(history_body[prompt_id], dict):
                outputs = history_body[prompt_id].get("outputs")
            elif "outputs" in history_body:
                outputs = history_body["outputs"]
            if outputs:
                break

    # 4. Extract an output filename and GET /api/view with redirect following +
    #    event-hook logging. Use a fresh client with hooks registered.
    filename = _first_output_filename(outputs or {})
    if not filename:
        evidence = (
            f"status body:\n{json.dumps(body, indent=2)[:800]}\n\n"
            f"history body:\n{json.dumps(history_body, indent=2)[:1500] if history_body else '(empty)'}"
        )
        _print_block(
            3,
            "redirect auth-stripping",
            "[WARN] no output filename found",
            "Job completed but no image filename surfaced in status or history outputs. "
            "History endpoint may require a different path or the response shape differs.",
            evidence,
        )
        return 1

    async with _client(key, event_hooks={"request": [log_request]}) as client:
        try:
            view = await client.get(
                f"{CLOUD_BASE_URL}/api/view",
                params={"filename": filename},
                follow_redirects=True,
            )
        except httpx.TransportError as e:
            _print_block(
                3,
                "redirect auth-stripping",
                "[FAIL] view transport error",
                f"{type(e).__name__}: {e}",
                json.dumps(logged, indent=2),
            )
            return 1

    # 4. Analyze logged requests — check each hop's transition from the PREVIOUS
    #    hop's host (not just from origin), so A->B->C where B and C differ catches
    #    the A->B transition even if C is same-host as A.
    if not logged:
        _print_block(
            3,
            "redirect auth-stripping",
            "[FAIL] no requests logged",
            "Event hook recorded zero requests; cannot analyze.",
            "(empty)",
        )
        return 1

    origin_host = logged[0]["host"]
    cross_host_leak = False  # a cross-host hop carried auth
    cross_host_leak_detail: tuple[str, str] | None = None  # (from_host, to_host)
    for i in range(1, len(logged)):
        prev_host = logged[i - 1]["host"]
        curr = logged[i]
        if curr["host"] != prev_host and (curr["x_api_key_present"] or curr["authorization_present"]):
            cross_host_leak = True
            cross_host_leak_detail = (prev_host, curr["host"])
            break

    redirect_requests = logged[1:]
    evidence = (
        f"View final status: {view.status_code}\nRequest trace (origin + redirects):\n{json.dumps(logged, indent=2)}"
    )

    if cross_host_leak:
        from_host, to_host = cross_host_leak_detail or ("?", "?")
        _print_block(
            3,
            "redirect auth-stripping",
            "[WARN] auth leaked",
            f"SECURITY CONCERN: httpx forwarded auth headers across hosts. "
            f"Redirect {from_host} -> {to_host} carried X-API-Key (or Authorization). "
            f"CloudBackend.view() must strip auth manually or use a two-hop fetch.",
            evidence,
        )
        return 0  # finding captured is a valid resolution

    if not redirect_requests:
        _print_block(
            3,
            "redirect auth-stripping",
            "[WARN] no redirect observed",
            f"/api/view returned {view.status_code} directly from {origin_host} "
            f"without a 302. Redirect-auth concern may not apply to this endpoint shape "
            f"in cloud.comfy.org's current behavior — could-not-trigger.",
            evidence,
        )
        return 1

    hosts_seen = {r["host"] for r in logged}
    if len(hosts_seen) == 1:
        _print_block(
            3,
            "redirect auth-stripping",
            "[OK] same-host redirect",
            f"Redirect stayed within {origin_host}; no cross-host hops. "
            f"Cross-host auth leak does not apply to this flow.",
            evidence,
        )
        return 0

    # Cross-host transitions occurred but no leak captured
    _print_block(
        3,
        "redirect auth-stripping",
        "[OK] auth stripped on cross-host redirect",
        f"Cross-host redirect observed ({origin_host} -> {logged[-1]['host']}); "
        f"httpx stripped auth headers on the transition. Safe to use follow_redirects=True "
        f"for this specific flow — but the behavior depends on the redirect target; "
        f"CloudBackend should still assert headers on integration tests.",
        evidence,
    )
    return 0


def _first_output_filename(outputs: dict) -> str | None:
    """Find the first filename in a history/outputs dict (cloud or local shape)."""
    if not isinstance(outputs, dict):
        return None
    for node in outputs.values():
        if isinstance(node, dict):
            for key in ("images", "videos", "audio"):
                items = node.get(key)
                if isinstance(items, list) and items:
                    first = items[0]
                    if isinstance(first, dict) and isinstance(first.get("filename"), str):
                        return first["filename"]
                    if isinstance(first, str):
                        return first
    return None


# ---------- Probe 4: 429 disambiguation ----------


async def probe_4(dry_run: bool) -> int:
    """Burst invalid workflows; record any 429 bodies. Invalid payload -> no credit spend."""
    key = _load_key()
    # Intentionally invalid: "prompt" must be an object (research SecA.3). Validation rejects fast,
    # so we don't pay for any job. If rate-limiter fires before validation, we see 429.
    invalid_payload = {"prompt": "not-an-object"}
    burst_count = 5

    if dry_run:
        print(f"[DRY RUN] Would POST {CLOUD_BASE_URL}/api/prompt x{burst_count} in tight loop")
        print(f"  Headers: X-API-Key: {_mask_key(key)}")
        print(f"  Body:    {invalid_payload}  (intentionally invalid -> no credit spend)")
        print("[DRY RUN] Record status + body of every response; flag any 429.")
        return 0

    async with _client(key) as client:
        tasks = [client.post(f"{CLOUD_BASE_URL}/api/prompt", json=invalid_payload) for _ in range(burst_count)]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

    results = []
    rate_limited = []
    for i, r in enumerate(responses):
        if isinstance(r, Exception):
            results.append(f"  [{i}] EXCEPTION: {type(r).__name__}: {r}")
            continue
        line = f"  [{i}] {r.status_code}  {r.text[:240]}"
        results.append(line)
        if r.status_code == 429:
            rate_limited.append(r)

    if rate_limited:
        bodies = "\n\n".join(r.text for r in rate_limited[:3])
        finding = (
            f"Observed {len(rate_limited)}/{burst_count} 429 responses. "
            f"Inspect body to determine whether `code`/`type` distinguishes rate-limit from "
            f"payment-lapsed (see SecA.3 three-shape parser)."
        )
        status = "[OK] 429 triggered"
        evidence = "All responses:\n" + "\n".join(results) + f"\n\n429 bodies:\n{bodies}"
        _print_block(4, "429 disambiguation", status, finding, evidence)
        return 0

    finding = (
        "Burst of 5 invalid submissions did not trigger 429 — rate-limit threshold likely "
        "higher than 5 req/s or applied only to valid jobs. Document as 'could-not-trigger' "
        "and rely on SecA.3 three-shape parser. CloudBackend should still handle 429 on the "
        "`code`/`type` path as a defensive measure."
    )
    evidence = "All responses:\n" + "\n".join(results)
    _print_block(4, "429 disambiguation", "[WARN] could-not-trigger", finding, evidence)
    return 1


# ---------- Probe 5: concurrency overflow on Standard plan ----------


async def probe_5(dry_run: bool) -> int:
    """Submit 3 valid workflows in quick succession; observe queue vs reject behavior."""
    key = _load_key()
    burst_count = 3

    if dry_run:
        print(f"[DRY RUN] Would POST {CLOUD_BASE_URL}/api/assets (upload ref image, free)")
        print(f"[DRY RUN] Would POST {CLOUD_BASE_URL}/api/prompt x{burst_count} minimal LoadImage->SaveImage jobs")
        print(f"  Headers: X-API-Key: {_mask_key(key)}")
        print(f"[DRY RUN] Record each response; all-accepted ~= {burst_count * 0.4:.1f} credits total.")
        print("[DRY RUN] If any reject at submit, near-zero credits.")
        return 0

    async with _client(key) as client:
        try:
            asset_name, _asset = await _upload_ref_asset(client)
        except (RuntimeError, httpx.HTTPStatusError, httpx.TransportError) as e:
            _print_block(
                5,
                "concurrency overflow",
                "[FAIL] asset upload failed",
                f"{type(e).__name__}: {e}",
                "Cannot proceed without a test asset.",
            )
            return 1

        # 3 distinct workflows (different filename_prefixes to avoid any server-side dedup)
        payloads = [
            {"prompt": _minimal_loadimage_workflow(asset_name, prefix=f"probe5_{i}")} for i in range(burst_count)
        ]
        tasks = [client.post(f"{CLOUD_BASE_URL}/api/prompt", json=p) for p in payloads]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

    results = []
    accepted = 0
    rejected = []
    for i, r in enumerate(responses):
        if isinstance(r, Exception):
            results.append(f"  [{i}] EXCEPTION: {type(r).__name__}: {r}")
            continue
        line = f"  [{i}] {r.status_code}  {r.text[:300]}"
        results.append(line)
        if r.status_code == 200:
            accepted += 1
        else:
            rejected.append((r.status_code, r.text[:500]))

    if accepted == burst_count:
        finding = (
            f"[OK] All {burst_count} submissions accepted (200) — cloud queues silently beyond the "
            f"concurrency cap. CloudBackend does not need client-side throttling. "
            f"Credits spent: ~{burst_count * 0.4:.1f} (minimal passthrough)."
        )
        status = f"[OK] queued silently (~{burst_count * 0.4:.1f} credits)"
    elif accepted > 0 and rejected:
        rejected_codes = ", ".join(str(c) for c, _ in rejected)
        finding = (
            f"Partial acceptance: {accepted}/{burst_count} submissions got 200, "
            f"{len(rejected)} rejected with codes [{rejected_codes}]. Cloud enforces concurrency "
            f"at submit time — CloudBackend should retry with backoff on these rejection codes."
        )
        status = f"[OK] partial reject ({accepted}/{burst_count} accepted)"
    else:
        finding = (
            f"All {burst_count} submissions rejected — unexpected. Likely auth/config issue rather "
            f"than concurrency enforcement. Inspect error bodies before drawing conclusions."
        )
        status = "[FAIL] all rejected"

    evidence = "Submission responses:\n" + "\n".join(results)
    _print_block(5, "concurrency overflow", status, finding, evidence)
    return 0


# ---------- Probe Real: real-pipeline verification ----------

CREDITS_PER_SECOND = 0.39  # Research doc §"Comfy Cloud Platform — Overview" (Dec 2025 pricing).
REAL_POLL_CAP_SECONDS = 300


def _patch_loadimage_nodes(workflow: dict, asset_hash: str) -> list[str]:
    """Overwrite every LoadImage node's `image` input with `asset_hash`. Returns patched node_ids."""
    patched = []
    for node_id, node in workflow.items():
        if isinstance(node, dict) and node.get("class_type") == "LoadImage":
            inputs = node.get("inputs")
            if isinstance(inputs, dict) and "image" in inputs:
                inputs["image"] = asset_hash
                patched.append(node_id)
    return patched


def _collect_output_filenames(outputs: Any) -> list[str]:
    """Flatten a history outputs dict into a list of filenames (images/videos/audio)."""
    files: list[str] = []
    if not isinstance(outputs, dict):
        return files
    for node in outputs.values():
        if not isinstance(node, dict):
            continue
        for key in ("images", "videos", "audio"):
            items = node.get(key)
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("filename"), str):
                    files.append(item["filename"])
    return files


async def probe_real(workflow_path: str, dry_run: bool) -> int:
    """Run a full API-format workflow end-to-end; report wall-time + credit estimate."""
    key = _load_key()

    path = Path(workflow_path)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.is_file():
        print(f"ERROR: workflow file not found: {path}", file=sys.stderr)
        return 2

    try:
        workflow = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: cannot read workflow JSON ({path}): {e}", file=sys.stderr)
        return 2

    if not isinstance(workflow, dict):
        print(f"ERROR: workflow JSON must be an object (API format), got {type(workflow).__name__}", file=sys.stderr)
        return 2

    loadimage_nodes = [
        nid for nid, node in workflow.items() if isinstance(node, dict) and node.get("class_type") == "LoadImage"
    ]

    if dry_run:
        try:
            rel = path.relative_to(REPO_ROOT)
        except ValueError:
            rel = path
        print(f"[DRY RUN] Workflow: {rel}")
        print(f"[DRY RUN] {len(workflow)} nodes; LoadImage nodes: {loadimage_nodes or 'none'}")
        if loadimage_nodes:
            ref = _find_reference_image()
            if ref:
                print(f"[DRY RUN] Would upload ref image: {ref.relative_to(REPO_ROOT)} and patch LoadImage.image")
            else:
                print("[DRY RUN] WARN: LoadImage nodes present but no PNG in output/ to upload")
        print(f"[DRY RUN] Would POST {CLOUD_BASE_URL}/api/prompt  (X-API-Key: {_mask_key(key)})")
        print(f"[DRY RUN] Then poll /api/job/<id>/status until terminal (cap {REAL_POLL_CAP_SECONDS}s)")
        print("[DRY RUN] Then GET /api/history/<id>; flatten outputs to filenames")
        print(f"[DRY RUN] Credit estimate: wall_time_seconds * {CREDITS_PER_SECOND} (per research doc)")
        return 0

    print(
        "WARNING: probe-real runs a live workflow. Credit spend unknown upfront "
        "(typical Flux run: 8-30 credits). Ctrl-C within 3s to abort.",
        file=sys.stderr,
    )
    await asyncio.sleep(3)

    t0 = time.monotonic()
    sub_body: dict | None = None
    status_body: dict = {}
    history_body: Any = None
    outputs: dict | None = None
    prompt_id: str = ""

    async with _client(key) as client:
        # 1. Upload ref image if workflow has LoadImage nodes.
        if loadimage_nodes:
            try:
                asset_hash, _asset = await _upload_ref_asset(client)
            except (RuntimeError, httpx.HTTPStatusError, httpx.TransportError) as e:
                _print_real_block(
                    path.name,
                    "[FAIL] ref asset upload failed",
                    f"{type(e).__name__}: {e}",
                    "Workflow has LoadImage node(s); need a PNG in output/ to upload.",
                )
                return 1
            patched = _patch_loadimage_nodes(workflow, asset_hash)
            print(
                f"Patched {len(patched)} LoadImage node(s) {patched} with asset_hash={asset_hash[:16]}...",
                file=sys.stderr,
            )

        # 2. Submit.
        try:
            sub = await client.post(f"{CLOUD_BASE_URL}/api/prompt", json={"prompt": workflow})
            sub.raise_for_status()
        except (httpx.HTTPStatusError, httpx.TransportError) as e:
            status_code = getattr(getattr(e, "response", None), "status_code", "transport")
            body_text = getattr(getattr(e, "response", None), "text", str(e))[:2000]
            _print_real_block(
                path.name,
                f"[FAIL] submit rejected ({status_code})",
                "Workflow failed validation at cloud /api/prompt. "
                "Likely cause: unsupported node type on cloud (check error body for VALIDATION_ERROR).",
                f"{status_code}\n{body_text}",
            )
            return 1

        sub_body = _parse_json_safe(sub)
        prompt_id = sub_body.get("prompt_id") if isinstance(sub_body, dict) else ""
        if not isinstance(prompt_id, str) or not prompt_id:
            _print_real_block(
                path.name,
                "[FAIL] no prompt_id in submit response",
                "Submit succeeded but response did not contain a string prompt_id.",
                sub.text[:500],
            )
            return 1

        node_errors = sub_body.get("node_errors") if isinstance(sub_body, dict) else None
        print(f"Submitted prompt_id={prompt_id} (node_errors={node_errors})", file=sys.stderr)

        # 3. Poll status to terminal (cap REAL_POLL_CAP_SECONDS).
        state: str | None = None
        iterations = REAL_POLL_CAP_SECONDS // 3
        for _ in range(iterations):
            await asyncio.sleep(3)
            try:
                stat = await client.get(f"{CLOUD_BASE_URL}/api/job/{prompt_id}/status")
                stat.raise_for_status()
            except (httpx.HTTPStatusError, httpx.TransportError) as e:
                status_code = getattr(getattr(e, "response", None), "status_code", "transport")
                body_text = getattr(getattr(e, "response", None), "text", str(e))[:500]
                _print_real_block(
                    path.name,
                    f"[FAIL] status polling failed ({status_code})",
                    "Could not determine job completion state.",
                    f"{status_code}\n{body_text}",
                )
                return 1
            parsed = _parse_json_safe(stat)
            if parsed is None:
                continue
            status_body = parsed
            state = status_body.get("status") or status_body.get("state")
            elapsed = time.monotonic() - t0
            print(f"  [{elapsed:5.1f}s] state={state}", file=sys.stderr)
            if state in ("completed", "success"):
                break
            if state in ("failed", "error", "cancelled"):
                _print_real_block(
                    path.name,
                    f"[FAIL] job {state} during execution",
                    f"Workflow validated at submit but did not complete. Wall time: {elapsed:.1f}s. "
                    f"Error: {status_body.get('error_message') or '(no error_message field)'}.",
                    json.dumps(status_body, indent=2)[:2000],
                )
                return 1
        else:
            elapsed = time.monotonic() - t0
            _print_real_block(
                path.name,
                f"[WARN] timed out after {REAL_POLL_CAP_SECONDS}s",
                f"Job did not reach terminal state. Last state={state}. Wall time: {elapsed:.1f}s.",
                f"last status:\n{json.dumps(status_body, indent=2)[:2000]}",
            )
            return 1

        wall_time_s = time.monotonic() - t0

        # 4. Fetch outputs via /api/history (per §A.6.2 evidence — history_v2 fallback).
        for path_suffix in (f"/api/history/{prompt_id}", f"/api/history_v2?prompt_id={prompt_id}"):
            try:
                hist = await client.get(f"{CLOUD_BASE_URL}{path_suffix}")
                hist.raise_for_status()
            except (httpx.HTTPStatusError, httpx.TransportError):
                continue
            parsed_hist = _parse_json_safe(hist)
            if parsed_hist is None:
                continue
            history_body = parsed_hist
            if isinstance(history_body.get("history"), list):
                for entry in history_body["history"]:
                    if isinstance(entry, dict) and entry.get("prompt_id") == prompt_id:
                        outputs = entry.get("outputs")
                        break
            elif prompt_id in history_body and isinstance(history_body[prompt_id], dict):
                outputs = history_body[prompt_id].get("outputs")
            if outputs:
                break

    output_files = _collect_output_filenames(outputs)
    est_credits = wall_time_s * CREDITS_PER_SECOND
    status = f"[OK] completed in {wall_time_s:.1f}s (~{est_credits:.1f} credits)"
    finding = (
        f"Workflow `{path.name}` executed end-to-end on cloud.comfy.org. "
        f"Wall time: {wall_time_s:.1f}s. Estimated credits: ~{est_credits:.1f} "
        f"(at {CREDITS_PER_SECOND} credits/s). Outputs: {len(output_files)} file(s). "
        f"Verifies full Flux inference path against cloud — no unsupported-node rejections."
    )
    evidence = (
        f"Submit response:\n{json.dumps(sub_body, indent=2)[:600]}\n\n"
        f"Final status:\n{json.dumps(status_body, indent=2)[:800]}\n\n"
        f"Output filenames ({len(output_files)}): {output_files[:5]}"
    )
    _print_real_block(path.name, status, finding, evidence)
    return 0


def _print_real_block(workflow_name: str, status: str, finding: str, evidence: str) -> None:
    """Paste-ready Markdown block for probe-real (parallels _print_block shape)."""
    print(f"### Probe REAL — {workflow_name}\n")
    print(f"**Status:** {status}\n")
    print(f"**Finding:** {finding}\n")
    print("**Raw evidence:**\n")
    print("```")
    print(evidence.rstrip())
    print("```")
    print()


# ---------- CLI ----------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Story 6.4 probe spike — cloud.comfy.org unknowns.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name in ("probe-2", "probe-3", "probe-4", "probe-5"):
        p = sub.add_parser(name, help=f"Run {name}")
        p.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the would-be request(s) with masked key and exit 0. Zero credits.",
        )

    p_real = sub.add_parser("probe-real", help="Run a real workflow end-to-end")
    p_real.add_argument(
        "--workflow",
        required=True,
        help="Path to an API-format workflow JSON (e.g. templates/image_flux2.json).",
    )
    p_real.add_argument(
        "--dry-run",
        action="store_true",
        help="Print plan + masked key; zero credits.",
    )

    args = parser.parse_args()

    if args.cmd == "probe-real":
        rc = asyncio.run(probe_real(args.workflow, args.dry_run))
    else:
        probes = {
            "probe-2": probe_2,
            "probe-3": probe_3,
            "probe-4": probe_4,
            "probe-5": probe_5,
        }
        rc = asyncio.run(probes[args.cmd](args.dry_run))
    sys.exit(rc)


if __name__ == "__main__":
    main()
