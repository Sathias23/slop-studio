# Comfy Cloud Integration

Architectural overview of slop-studio's cloud backend: the compatibility surface, credit handling, error taxonomy, and the design decisions that shaped the integration.

## Overview

Comfy Cloud is Comfy Org's hosted ComfyUI service. The key insight that shaped slop-studio's integration is that **the Comfy Cloud API is intentionally protocol-compatible with local ComfyUI** — same workflow JSON format, same outputs shape, same `prompt_id` contract. Every existing slop-studio template runs on both backends without modification; the MCP tool surface (`queue_prompt`, `check_next_job`, `get_image`) stays unchanged.

The abstraction lives in `slop_studio/backends/` as an `abc.ABC` subpackage with `base.py`, `local.py`, `cloud.py`, and `router.py`. Only four structural differences between backends needed bridging: an `/api/` path prefix, a two-call status flow (status + history), a 302-follow on image download, and a hash-addressed asset upload API for image inputs. Routing is resolved per submission by inspecting the template's optional `backend` field (`"local"` / `"cloud"` / `"either"`), with `SLOP_STUDIO_DEFAULT_BACKEND` as the fallback. `prompt_id` values are self-describing (`"local:<uuid>"` / `"cloud:<uuid>"`) so `check_next_job` and `get_image` route without persistent state.

No new Python dependencies were required — `httpx` handles the REST flow, `respx` (already a dev dep) handles the test mocks. Getting started is opt-in: set `COMFY_CLOUD_API_KEY` (or add an entry to `~/.config/slop-studio/credentials.json`), tag a template with `"backend": "cloud"`, and submit as normal.

## Local ↔ Cloud compatibility

| Capability | Local ComfyUI | Comfy Cloud | Impact |
|---|---|---|---|
| Workflow submission path | `POST /prompt` | `POST /api/prompt` | Backend-specific URL builder |
| Workflow JSON shape | API format | Same API format | Templates reusable without transformation |
| Auth | None | `X-API-Key` header | Per-backend client config |
| Status check | `GET /history/{id}` (unified) | `GET /api/job/{id}/status` + `GET /api/history_v2/{id}` | Two-call flow hidden by abstraction |
| Output schema | `{node_id: {images: [...]}}` | Same shape | Output-parsing logic reusable |
| Image retrieval | `GET /view` returns bytes | `GET /api/view` returns 302 to a signed URL | `follow_redirects=True` required |
| Image input upload | `POST /upload/image` (multipart) | `POST /api/asset` (hash-addressed, dedup) | Per-backend upload helper |
| Realtime progress | WS on local host | WSS with token query param | Polling used; WS optional |
| Concurrency | Local GPU limit | 1 / 3 / 5 by plan tier | Server-side queue; no client throttling |
| Seed behavior | Random at submit | Same | No change |
| Timeouts | User-controlled | 30 min – 1 hour cap by plan | Raised sensible `MAX_POLL_DURATION` |
| Credits | N/A | Billed per GPU-second | New surface (see below) |

## Credits and account management

Comfy Cloud bills per GPU-second (approximately 0.39 credits/second as of the Dec 2025 pricing update). Every account has a single unified balance consumed by workflow runs and by Partner Nodes. Plan tiers also cap concurrent jobs: Free/Standard = 1, Creator = 3, Pro = 5. An active paid subscription is required to run workflows — free accounts can authenticate but not submit.

slop-studio deliberately does **not** poll for credit balance. The official `GET /api/user` endpoint returns only `{"status": "active|waitlisted"}` — no balance field — and no other documented endpoint surfaces credits. Instead, the design surfaces HTTP 402 "Insufficient credits" from `POST /api/prompt` as a new `no_credits` terminal error (the moment it actually matters), and ships the `open_comfy_cloud_portal` MCP tool so Claude can offer "click here to top up" directly in the conversation.

The portal URL (`https://platform.comfy.org/`) is also where API keys are issued. Keys are shown **once** at creation and cannot be retrieved later — rotation requires re-issue. slop-studio masks the key in every error preview so a 401/403 response body that echoes the key back does not leak it into the conversation.

## Error codes

Four new terminal error reason codes map cloud HTTP failures onto slop-studio's existing `terminal_error` / `transient_error` taxonomy. The authoritative mapping lives in `slop_studio/backends/cloud.py:_submit_error_to_dict`.

| Error | Trigger | User action |
|---|---|---|
| `auth_failed` | 401 — API key missing, invalid, or unregistered | Verify `COMFY_CLOUD_API_KEY`; regenerate at the portal |
| `no_credits` | 402 — insufficient credits for this run | Call `open_comfy_cloud_portal` to top up |
| `account_issue` | 403, or 429 whose body `code` mentions `payment` / `billing` / `account` / `subscription` | Call `open_comfy_cloud_portal` to resolve billing |
| `rate_limited` | 429 — exceeded the plan tier's concurrent-job cap | Wait and retry |

The error messages for `no_credits`, `account_issue`, and router-layer `auth_failed` name `open_comfy_cloud_portal` verbatim, so Claude can follow the UX contract without guessing the recovery tool. The 429 disambiguation (`rate_limited` vs `account_issue`) is driven by the response body `code` field: substrings `payment` / `billing` / `account` / `subscription` route to `account_issue`; everything else stays `rate_limited`. Absent body falls through to `rate_limited`.

Per the project's NFR-C5, there is no auto-retry or silent backend fallback — the user decides whether to re-submit. Transient 5xx cloud errors surface as `transient_error("unreachable")`, preserving the local-backend convention.

## Design decisions

- **`abc.ABC` for the `Backend` interface**, not `typing.Protocol`. Both implementations are in-tree and benefit from shared inheritance helpers (thumbnail generation, output writing, error wrapping). Resist the temptation to over-abstract for a hypothetical third backend until a concrete one shows up.
- **Self-describing `prompt_id` prefixes** (`"local:"` / `"cloud:"`). `check_next_job` and `get_image` route without a persistent registry; absent prefix → local, preserving backwards compatibility with pre-0.3.2 ids.
- **No auto-fallback between backends.** On a cloud error, the user retries explicitly. Avoids burning credits or local GPU time unexpectedly and keeps the provenance clear — every error payload is tagged with its originating backend name.
- **Credentials colocated with Bluesky credentials** in `~/.config/slop-studio/credentials.json`. Explicitly avoids per-project `.mcp.json` credential blocks — a setup friction pattern the project has deliberately moved away from. The env var `COMFY_CLOUD_API_KEY` wins when both are set.
- **Per-template routing declared in `.meta.json`.** Three optional fields (`backend`, `output_keys`, `cloud_estimate_credits`) future-proof template authoring without breaking existing templates. See `templates/README.md` for the field reference.
- **PR #4 (the `CloudBackend` implementation) was blocked on a short probe spike** with a real API key. The five open questions (credit balance endpoint, LoadImage asset reference, redirect auth-stripping, 429 disambiguation, concurrency overflow) were answered before freezing the surface. Defensive tests lock the behavior in case the experimental API drifts.

## When to use cloud vs local

Cloud is the right choice when you don't have a local GPU, want to run models that exceed your VRAM (the stock `image_flux2` template's full-precision FP8-mixed Flux 2 Dev weights don't fit in 16 GB), or want parallelism beyond what your hardware supports. Local is the right choice for iteration-heavy work where credit costs would add up, for workflows that depend on custom nodes Comfy Cloud doesn't provision, or for models Comfy Cloud rejects — notably GGUF-quantized weights via `UnetLoaderGGUF`. The shipped `flux2_klein` family is tagged `"backend": "local"` for exactly that reason.

The `"either"` option on a template's `backend` field defers the choice to `SLOP_STUDIO_DEFAULT_BACKEND`, so a single template can be moved between backends by flipping one env var. This is useful for templates whose models and nodes are supported on both sides.

## References

- [Comfy Cloud API Overview](https://docs.comfy.org/development/cloud/overview) — official API docs
- [Comfy Cloud Pricing](https://www.comfy.org/cloud/pricing) — plan tiers and credit costs
- [Comfy Cloud Billing](https://support.comfy.org/hc/en-us/articles/42819199299732-Billing-on-Comfy-Cloud) — credit semantics
- [platform.comfy.org/profile/api-keys](https://platform.comfy.org/profile/api-keys) — API key issuance

Internal: the full research document lives at `_bmad-output/planning-artifacts/research/technical-comfy-cloud-integration-research-2026-04-14.md`. That path is gitignored, so external readers will see a broken link — it's source material for maintainers, not public documentation.
