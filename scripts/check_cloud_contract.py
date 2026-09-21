"""check_cloud_contract.py — contract test for the Comfy Cloud REST API.

Comfy Cloud's API was undocumented when ``CloudBackend`` was first written, so
its behaviour was established empirically (see ``scripts/probe_cloud.py``).
Comfy Org has since published an official OpenAPI spec — ``openapi-cloud.yaml``
in the ``Comfy-Org/docs`` repo — so we can now assert, cheaply and without a
funded key, that the endpoints/fields ``slop_studio/backends/cloud.py`` depends
on still exist and have not silently changed shape.

This is the *primary* early-warning for cloud drift. It costs zero credits and
needs no API key. A separate, optional live smoke test (``probe_cloud.py
probe-real`` via ``cloud-smoke.yml``) covers *behavioural* drift the schema
cannot — the 302 auth-strip on ``/api/view`` and the 429 billing/rate-limit
ambiguity.

The contract below is keyed to each ``CloudBackend`` call site. When a check
fails, fix ``cloud.py`` (or, if the change is upstream and intended, update both
``cloud.py`` and this contract) — the failure means the integration is about to
break, or already has.

Usage:
    # Check against the live published spec (default — what CI's weekly run does):
    python scripts/check_cloud_contract.py

    # Check against a local/pinned copy (offline, deterministic):
    python scripts/check_cloud_contract.py --spec tests/contract/openapi-cloud.yaml

Exit codes:
    0 — every used endpoint/field is present (deprecation warnings are non-fatal)
    1 — a contract breach: a used endpoint or required field is missing/changed
    2 — could not load the spec (network/parse error)
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Any

# Default to the spec the official Cloud API Reference is generated from.
LIVE_SPEC_URL = "https://raw.githubusercontent.com/Comfy-Org/docs/main/openapi-cloud.yaml"

# Status values CloudBackend.status() relies on /api/job/{id}/status returning
# (the JobStatusResponse enum). If cloud renames any of these, status mapping
# silently breaks — so we assert the documented enum still contains them.
REQUIRED_JOB_STATUS_VALUES = {
    "waiting_to_dispatch",
    "pending",
    "in_progress",
    "completed",
    "error",
    "cancelled",
}

# Endpoints CloudBackend calls, by (method, spec-path). Spec paths use {job_id};
# the prompt_id returned by /api/prompt is the same value (job_id == prompt_id).
USED_ENDPOINTS = [
    ("post", "/api/prompt"),
    ("get", "/api/job/{job_id}/status"),
    ("get", "/api/jobs/{job_id}"),
    ("get", "/api/view"),
    ("post", "/api/assets"),
]


@dataclass
class Result:
    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: str = ""


# --------------------------------------------------------------------------- #
# OpenAPI navigation helpers (resolve $ref + flatten allOf)
# --------------------------------------------------------------------------- #


def _resolve(spec: dict, node: Any) -> Any:
    """Follow a local ``$ref`` (``#/components/schemas/X``) one or more hops."""
    seen = set()
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if ref in seen:  # cycle guard
            return {}
        seen.add(ref)
        parts = ref.lstrip("#/").split("/")
        node = spec
        for part in parts:
            node = node.get(part, {}) if isinstance(node, dict) else {}
    return node


def _properties(spec: dict, schema: Any) -> dict:
    """Return ``name -> property-schema``, merging ``allOf`` and resolving refs."""
    schema = _resolve(spec, schema)
    if not isinstance(schema, dict):
        return {}
    props: dict[str, Any] = {}
    for sub in schema.get("allOf", []):
        props.update(_properties(spec, sub))
    direct = schema.get("properties")
    if isinstance(direct, dict):
        props.update(direct)
    return props


def _required(spec: dict, schema: Any) -> set[str]:
    """Return the set of required property names, merging ``allOf`` and refs."""
    schema = _resolve(spec, schema)
    if not isinstance(schema, dict):
        return set()
    req: set[str] = set()
    for sub in schema.get("allOf", []):
        req |= _required(spec, sub)
    direct = schema.get("required")
    if isinstance(direct, list):
        req |= set(direct)
    return req


def _operation(spec: dict, method: str, path: str) -> dict | None:
    op = spec.get("paths", {}).get(path, {}).get(method)
    return op if isinstance(op, dict) else None


def _json_response_schema(spec: dict, op: dict, status: str) -> Any:
    resp = op.get("responses", {}).get(status, {})
    return resp.get("content", {}).get("application/json", {}).get("schema")


def _request_schema(spec: dict, op: dict) -> Any:
    return op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")


def _params(op: dict) -> dict[str, dict]:
    return {p["name"]: p for p in op.get("parameters", []) if isinstance(p, dict) and "name" in p}


# --------------------------------------------------------------------------- #
# Contract checks
# --------------------------------------------------------------------------- #


def _check(name: str, ok: bool, detail: str = "") -> Result:
    """Build a pass/fail Result; ``detail`` is only attached on failure."""
    return Result(name, "ok" if ok else "fail", "" if ok else detail)


def check_contract(spec: dict) -> list[Result]:
    """Run every contract assertion against ``spec``; return one Result each."""
    results: list[Result] = []

    # 1. Every endpoint CloudBackend uses must exist, and we warn if it has been
    #    marked deprecated (this is exactly how the history_v2 -> jobs drift would
    #    have been caught early).
    ops: dict[tuple[str, str], dict] = {}
    for method, path in USED_ENDPOINTS:
        op = _operation(spec, method, path)
        label = f"{method.upper()} {path}"
        if op is None:
            results.append(Result(f"endpoint {label}", "fail", "not present in spec"))
            continue
        ops[(method, path)] = op
        if op.get("deprecated") is True:
            results.append(Result(f"endpoint {label}", "warn", "marked deprecated — plan a migration"))
        else:
            results.append(Result(f"endpoint {label}", "ok"))

    # 2. POST /api/prompt — submit() sends {"prompt": ...} and reads prompt_id /
    #    node_errors back.
    if op := ops.get(("post", "/api/prompt")):
        req = _resolve(spec, _request_schema(spec, op))
        has_prompt = "prompt" in (req.get("required") or [])
        results.append(
            _check("POST /api/prompt request requires `prompt`", has_prompt, f"required={req.get('required')}")
        )

        resp_props = _properties(spec, _json_response_schema(spec, op, "200"))
        for field in ("prompt_id", "node_errors"):
            results.append(_check(f"POST /api/prompt 200 has `{field}`", field in resp_props))

        # Advisory: cloud documents 429 on submit as *billing*, not rate-limit.
        # cloud.py defaults 429 to rate_limited unless the body code says billing —
        # surface the documented semantic so the heuristic stays honest.
        desc = (op.get("responses", {}).get("429", {}) or {}).get("description", "")
        if desc:
            results.append(Result("POST /api/prompt 429 documented semantic", "warn", f"spec says: {desc!r}"))

    # 3. GET /api/job/{job_id}/status — status() maps the JobStatusResponse enum.
    if op := ops.get(("get", "/api/job/{job_id}/status")):
        props = _properties(spec, _json_response_schema(spec, op, "200"))
        enum = set((props.get("status") or {}).get("enum") or [])
        missing = REQUIRED_JOB_STATUS_VALUES - enum
        detail = f"missing {sorted(missing)} (enum={sorted(enum)})"
        results.append(_check("job status enum covers mapped states", not missing, detail))
        results.append(_check("job status response has `error_message`", "error_message" in props))

    # 4. GET /api/jobs/{job_id} — history() reads `outputs` off the flat envelope.
    if op := ops.get(("get", "/api/jobs/{job_id}")):
        props = _properties(spec, _json_response_schema(spec, op, "200"))
        results.append(_check("GET /api/jobs/{job_id} 200 has `outputs`", "outputs" in props, f"props={sorted(props)}"))

    # 5. GET /api/view — view() needs the `filename` param and the 302 redirect.
    if op := ops.get(("get", "/api/view")):
        fn = _params(op).get("filename")
        results.append(_check("GET /api/view has required `filename` param", bool(fn and fn.get("required"))))
        results.append(_check("GET /api/view documents 302 redirect", "302" in op.get("responses", {})))

    # 6. POST /api/assets — upload_asset() returns `asset_hash` from both the
    #    201 fresh-upload and 200 dedup-hit responses, so assert each carries it.
    #    The field is documented but NOT in the schema's `required` list, while
    #    upload_asset() treats a missing/empty hash as a hard failure (it raises
    #    ValueError) — warn so that latent gap stays visible. We don't fail on it:
    #    asset_hash has never been marked required upstream, so a hard assertion
    #    would be a permanent red rather than drift detection.
    if op := ops.get(("post", "/api/assets")):
        for status in ("201", "200"):
            schema = _json_response_schema(spec, op, status)
            props = _properties(spec, schema)
            present = "asset_hash" in props
            results.append(_check(f"POST /api/assets {status} has `asset_hash`", present, f"props={sorted(props)}"))
            if present and "asset_hash" not in _required(spec, schema):
                results.append(
                    Result(
                        f"POST /api/assets {status} `asset_hash` not guaranteed",
                        "warn",
                        "documented but not in `required`; upload_asset() treats it as mandatory",
                    )
                )

    return results


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _load_spec(source: str) -> dict:
    """Load the OpenAPI spec from a URL (http/https) or a local file path."""
    import yaml  # dev/CI-only dependency

    if source.startswith(("http://", "https://")):
        import httpx

        text = httpx.get(source, timeout=30.0, follow_redirects=True).raise_for_status().text
    else:
        with open(source, encoding="utf-8") as fh:
            text = fh.read()
    spec = yaml.safe_load(text)
    if not isinstance(spec, dict):
        raise ValueError(f"spec did not parse to a mapping: {type(spec).__name__}")
    return spec


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--spec",
        default=LIVE_SPEC_URL,
        help="OpenAPI spec URL or local path (default: the live published spec).",
    )
    args = parser.parse_args()

    try:
        spec = _load_spec(args.spec)
    except Exception as exc:  # surface any load failure as exit 2
        print(f"ERROR: could not load spec from {args.spec!r}: {exc}", file=sys.stderr)
        return 2

    results = check_contract(spec)
    symbols = {"ok": "PASS", "warn": "WARN", "fail": "FAIL"}
    print(f"Comfy Cloud API contract — checked against {args.spec}\n")
    for r in results:
        line = f"  [{symbols[r.status]}] {r.name}"
        if r.detail:
            line += f" — {r.detail}"
        print(line)

    fails = [r for r in results if r.status == "fail"]
    warns = [r for r in results if r.status == "warn"]
    passed = len(results) - len(fails) - len(warns)
    print(f"\n{len(results)} checks: {passed} pass, {len(warns)} warn, {len(fails)} fail")
    if fails:
        print("\nCONTRACT BREACH — the cloud API no longer matches what cloud.py expects.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
