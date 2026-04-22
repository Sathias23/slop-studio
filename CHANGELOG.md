# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.5.1] - 2026-04-22

### Fixed

- `post_to_bluesky` now emits AT Protocol **link facets** for URLs in the post text, not just for hashtags. Previously a post containing `github.com/Sathias23/slop-studio` rendered as plain, unclickable text in every Bluesky client — the official composer's auto-linkify only fires for posts authored in its own UI. The fix detects three URL shapes (scheme-prefixed `https://…` / `http://…`, `www.…`, and bare `domain.tld/path` across a conservative TLD allowlist), strips trailing sentence punctuation, and prepends `https://` to bare-domain matches so the facet uri is always well-formed. Version strings (`v0.5.0`) and filenames (`README.md`) are deliberately not linkified.

## [0.5.0] - 2026-04-22

### Added

- **OpenAI GPT Image 2 starter templates** — `api_openai_gpt_image_2_t2i` (text-to-image) and `api_openai_gpt_image_2_image_edit` (single-reference edit). Both tagged `backend: "local"` since Comfy Cloud doesn't yet ship gpt-image-2. Aspect ratio uses the shared 10-label vocabulary (`1:1, 3:2, 2:3, 4:3, 3:4, 5:4, 4:5, 16:9, 9:16, 21:9`) mapped to OpenAI's three documented sizes (1024x1024, 1536x1024, 1024x1536); the sidecar descriptions call out the collapse so users aren't surprised when e.g. `16:9` and `21:9` both resolve to `1536x1024`.
- **Starter-template cross-file integrity canary** (`tests/test_templates.py::test_starter_template_meta_matches_workflow`) — parametrized over every shipped starter pair, asserts that node_ids and fields referenced by each `.meta.json` actually exist in the paired workflow JSON. Catches drift that `_validate_metadata` can't see on its own.
- `scripts/smoke_gpt_image_2.py` — one-shot live harness that submits a low-quality GPT Image 2 job to local ComfyUI and polls to completion. Intended for manual verification after template changes.

### Fixed

- **Local-backend submissions of partner-API templates now forward `extra_data.api_key_comfy_org` on the `/prompt` payload**. Previously every `api_*` template (Flux 2 Pro, Nano Banana Pro, GPT Image 2) ran locally would 403 at execution with `Unauthorized: Please login first to use this node` because `cloud.py:201` attaches the key only on cloud submissions — partner nodes proxy through ComfyUI's account-API infrastructure even when executed locally. The fix scans the prepared workflow for known partner `class_type`s (`OpenAIGPTImage1`, `Flux2ProImageNode`, `GeminiImage2Node`), reuses the existing `comfy_cloud.api_key` credential (no new env var or credential slot), and fails fast with a clear `auth_failed` error *before* the image-upload step when the key is missing. Non-partner workflows keep their payload shape unchanged.
- `_randomize_seeds` capped at int32 max (`2**31 - 1`) instead of int64. The `OpenAIGPTImage1` node validates `seed` as int32 and rejects anything larger with `value_bigger_than_max` at submission. 2.1B unique values remains plenty of cache-collision headroom, and int32 values are valid int64 inputs so the cap is safe for every other node in the catalogue.

### Changed

- `slop-studio auth --comfy-cloud` prompt copy and argparse help now explain the key's dual role — it's required for Comfy Cloud submissions AND for any local workflow that uses a Comfy partner-API node (OpenAI GPT Image, Flux 2 Pro, Gemini/Nano Banana, etc.). Surfacing this at the auth prompt means users don't have to reverse-engineer why their first `api_*` template run failed.

## [0.4.5] - 2026-04-18

### Added

- All three Nano Banana Pro templates (`api_nano_banana_pro_text_to_image`, `api_nano_banana_pro_1img`, `api_nano_banana_pro_2img`) now expose the full ten-ratio Gemini 3 Pro Image set — `1:1, 3:2, 2:3, 4:3, 3:4, 5:4, 4:5, 16:9, 9:16, 21:9` — via the top-level `aspect_ratio` argument of `queue_prompt`. Previously aspect ratio was baked into the workflow JSON and required a manual template edit
- Template meta-schema gains a `field_map` alternative on `resolution_nodes` entries: `{"node_id", "field_map": {src_key: dest_field}}`. Used by API-node templates whose ratio input is a string field on the node (e.g. Gemini's `aspect_ratio: "3:4"`) rather than integer width/height. The legacy `width_field`/`height_field` pair keeps working unchanged; mixing both shapes on a single entry is rejected at `add_template`/`update_template` time

### Changed

- Consolidated the two template directories into one canonical location at `slop_studio/assets/starter-templates/`. The top-level `templates/` was deleted — it had drifted from the package-asset copy (stale descriptions, different placeholder filenames) and forced every template change into two places. `TEMPLATES_DIR` default now resolves to the package asset; the `.mcpb` packager no longer ships a separate top-level `templates/` directory (starter-templates already rides along inside the `slop_studio/` package copy). Consumer projects are unaffected — `slop-studio init` still scaffolds a project-local `templates/` from the package asset, and the per-project `SLOP_STUDIO_TEMPLATES_DIR` override still wins

## [0.4.4] - 2026-04-18

### Fixed

- Cloud-backend submissions now forward the ComfyUI account API key in `extra_data.api_key_comfy_org` on every `/api/prompt` call, so partner-API nodes (Flux2Pro, Nano Banana Pro, etc.) authenticate upstream. Previously every `api_*` template was accepted by the cloud (returning a valid `prompt_id`) but then failed at execution with `Unauthorized: Please login first to use this node`, because only the `X-API-Key` header was being sent and partner nodes look elsewhere for their auth. Reuses the existing `COMFY_CLOUD_API_KEY` — it's the same key at platform.comfy.org for both REST auth and partner-node billing. No config changes required

## [0.4.3] - 2026-04-18

### Added

- `api_nano_banana_pro_text_to_image` starter template — pure text-to-image variant of the Nano Banana Pro (Gemini 3 Pro Image) workflow with no reference image input. Uses the same `GeminiImage2Node` as the 1img / 2img variants but omits the `images` field (the API-node schema marks it optional). Tagged `backend: "cloud"`. Shipped as a starter so new projects scaffolded via `slop-studio init` pick it up alongside the other cloud templates

## [0.4.2] - 2026-04-18

### Fixed

- Cloud backend registration is now **lazy**: the MCP server picks up a newly-configured `COMFY_CLOUD_API_KEY` / `credentials.json` entry on the next tool call, without requiring a process restart. Previously, `slop_studio/backends/router.py` captured the key at module import time, so running `slop-studio auth --comfy-cloud` mid-session produced persistent `auth_failed` errors until the user fully quit and reopened Claude Code / Desktop. Key rotation also takes effect on the next call — the old `CloudBackend` instance is replaced in-place
- `check_next_job` and `get_image` on the cloud path now map 401/402/403/429 through the Story 6.7 error taxonomy (`auth_failed`, `no_credits`, `account_issue`, `rate_limited`) instead of blanket-wrapping every HTTP error as `transient_error("unreachable")`. Previously an auth failure mid-polling read like "cloud is down — retry" and Claude would usefully retry several times before giving up. The submit path has always mapped correctly; this brings the other two router-level cloud calls into line via a new public `CloudBackend.http_error_to_dict` helper. 5xx responses still correctly surface as transient `unreachable`
- `CloudBackend.history()` switched from `/api/history/{id}` to `/api/history_v2/{id}`. The v1 endpoint rejects `X-API-Key` auth with `401 "authentication method not allowed"` (it only accepts web session cookies), while v2 accepts the API key the rest of the backend already uses. The probe-spike §A.7 item 4 finding was collected from a session-cookie-authenticated context and didn't hold under API-key auth. Without this fix, every successful cloud submission produced a 401 on the follow-up `check_next_job` / `get_image` call even though the cloud had completed the job

## [0.4.1] - 2026-04-18

### Changed

- `slop-studio auth` now configures Bluesky and/or Comfy Cloud credentials with **merge-not-clobber** semantics — configuring one service never wipes the other. Interactive menu by default; `--bluesky`, `--comfy-cloud`, or `--all` flags bypass the menu. Existing non-empty blocks trigger a per-service overwrite confirmation that defaults to **No** (stray Enter keeps the existing entry). Writes are atomic (temp-file + `os.replace`) and reassert `0o600` on replacement. Malformed `credentials.json` — including valid JSON whose top-level or per-service value isn't an object — is surfaced as a terminal error with a manual-fix hint (the file is never auto-deleted). `EOF` on any prompt exits `1` with a clean error instead of a traceback

## [0.4.0] - 2026-04-18

### Added

- `open_comfy_cloud_portal` MCP tool opens `https://platform.comfy.org/` in the default browser — the realization of the pointer already referenced in Story 6.7's `no_credits` / `account_issue` / `auth_failed` error messages. Pure URL opener: no authentication, no API key, no cloud config required (Story 6.8)
- **Comfy Cloud backend support** (Epic 6): pluggable execution backend behind a per-template `backend` routing field, configured via `COMFY_CLOUD_API_KEY` env var or `credentials.json`. Adds the `open_comfy_cloud_portal` MCP tool, cloud-specific error codes (`auth_failed`, `no_credits`, `account_issue`, `rate_limited`), and three optional `.meta.json` fields (`backend`, `output_keys`, `cloud_estimate_credits`). See [docs/comfy-cloud-integration.md](docs/comfy-cloud-integration.md) for the architectural overview and design rationale (Story 6.9)
- Seven new cloud starter templates scaffolded into every new project by `slop-studio init`: three Flux.2 [pro] API variants (`api_flux2_pro_1img` / `_2img` / `_4img`), two Google Gemini 3 Pro Image (Nano Banana Pro) variants (`api_nano_banana_pro_1img` / `_2img`), a Flux.2 Dev FP8-mixed text-to-image workflow (`image_flux2_text_to_image`), and a promotion of `image_flux2` to a starter template. All tagged `"backend": "cloud"`. The canary test that used to require every starter be `backend=local` has been widened to require any explicit backend (`local` or `cloud`) — preventing starters from silently inheriting `SLOP_STUDIO_DEFAULT_BACKEND`

### Fixed

- Cloud starter-template input filenames namespaced to avoid collision across templates in a shared ComfyUI input dir (`api_flux2_pro_4img`); description of `api_flux2_pro_1img` corrected to reflect the single-slot `BatchImagesNode` routing (Greptile review)

## [0.3.6] - 2026-04-17

### Added

- Four new terminal error reason codes for Comfy Cloud failures: `auth_failed` (401), `no_credits` (402), `account_issue` (403), and `rate_limited` (429). The messages for `no_credits` / `account_issue` / router `auth_failed` name `open_comfy_cloud_portal` so Claude can call it directly once Story 6.8 ships the tool (Story 6.7)
- Optional `backend` kwarg on `terminal_error` / `transient_error` helpers. When set, the returned dict gains a `"backend"` key (`"local"` / `"cloud"`) giving Claude single-glance provenance when a tool fails mid-batch. Absent kwarg preserves the original four-key shape, so all existing callsites stay compatible (Story 6.7)
- 429 disambiguation via the response body `code` field — `payment`, `billing`, `account`, or `subscription` substrings surface as `account_issue` (user must top up / fix billing); everything else stays `rate_limited` (wait and retry) (Story 6.7)
- Defensive API-key scrubbing on cloud 403 body previews — any raw key echoed in the response body is replaced with its masked form before entering the user-visible error message (NFR-C3) (Story 6.7)

### Changed

- Cloud 429 is now terminal (`rate_limited` / `account_issue`) instead of the Story 6.4 placeholder `transient_error("unreachable")`. Per NFR-C5 there is no auto-retry or backend fallback at the tool-handler layer; Claude decides whether to re-submit (Story 6.7)
- Cloud 401 maps to `auth_failed` instead of the Story 6.4 placeholder `invalid_inputs`, with a masked key in the message and a link to the platform portal (Story 6.7)
- `safe_tool`'s `internal_error` and router caller-input errors (unknown backend, empty batch, mixed-backend batch, unknown prompt_id prefix) deliberately stay untagged. Their layer has no single-backend provenance, so a `"backend"` key would mislead. A canary test locks this scope boundary (Story 6.7)

### Fixed

- `_parse_error_body` now coerces `body["code"]` to `str` so numeric codes don't crash `_is_account_issue_code`'s `.lower()` call (Story 6.7 code review)
- Cloud error previews on the 400 and 422 paths are now sliced to `[:200]`, matching the 402/403/429 handling — stops a multi-kilobyte cloud response from inflating the error dict (Story 6.7 code review)
- Router `_prepare_and_submit` now tags errors with `backend=backend.name` instead of the hardcoded cloud helpers, so a future non-cloud backend routed through this function won't be mislabelled as `"cloud"` (Story 6.7 code review)
- `manifest.json` re-synced to match `pyproject.toml` after the Story 6.6 release missed it — unblocks the `build-mcpb` test suite (Story 6.7 code review)

## [0.3.5] - 2026-04-17

### Added

- Optional `.meta.json` fields: `backend` (`"local" | "cloud" | "either"`), `output_keys` (`list[str]`), and `cloud_estimate_credits` (non-negative number). `add_template` / `update_template` validate each on write; absent-by-default keeps existing templates working unchanged (Story 6.6)
- `list_templates` entries now include a `backend` field, defaulting to `"local"` when absent; `get_template` normalizes the same default in its response (Story 6.6)

### Changed

- `route_submission` consults the template's declared `backend` when no `backend_override` is passed: `"local"` / `"cloud"` lock the submission to that backend, `"either"` and absent/unreadable meta fall through to `DEFAULT_BACKEND_NAME`. `backend_override` still wins over the template's declaration (Story 6.6)
- Shipped starter templates (`flux2_klein`, `flux2_klein_ultrawide`, `flux2_klein_edit`) now declare `"backend": "local"` — `UnetLoaderGGUF` is rejected by Comfy Cloud, so explicit tagging prevents silent regressions for users running with `SLOP_STUDIO_DEFAULT_BACKEND=cloud` (Story 6.6)

## [0.3.4] - 2026-04-17

### Added

- `config.COMFY_CLOUD_URL`, `config.DEFAULT_BACKEND`, and `config.get_comfy_cloud_api_key()` — Comfy Cloud API key, base URL, and default backend now resolve through the same `env → credentials.json → config.toml → default` chain used by Bluesky credentials. `~/.config/slop-studio/credentials.json` accepts a `comfy_cloud.api_key` entry alongside the existing `bluesky` block (Story 6.5)
- `auth_failed` terminal error reason at the cloud-not-registered branch, with guidance naming both credential surfaces (env var + `credentials.json`). Story 6.7 will add the remaining new reason codes (Story 6.5)

### Changed

- `slop_studio.backends.router` reads cloud config via the new `slop_studio.config` getters instead of raw `os.environ` reads. `DEFAULT_BACKEND_NAME` is now sourced from `config.DEFAULT_BACKEND`, so `SLOP_STUDIO_DEFAULT_BACKEND=cloud` routes default submissions to cloud when a key is configured (Story 6.5)

## [0.3.3] - 2026-04-17

### Added

- `CloudBackend` (Comfy Cloud REST API) registered behind the `COMFY_CLOUD_API_KEY` env flag. `check_next_job` and `get_image` round-trip the `"cloud:<id>"` prefix; submissions still default to local (Story 6.4)
- `image_flux2` cloud template and `scripts/probe_cloud.py` probe-real script for validating the cloud API contract (Story 6.4)

## [0.3.2] - 2026-04-16

### Changed

- Returned `prompt_id` values from `queue_prompt` now carry a `"local:"` backend prefix (e.g. `"local:abc-123"`). `check_next_job` and `get_image` accept both the prefixed form and legacy bare ids — callers holding pre-0.3.2 ids continue to work (Story 6.3)
- Introduced a backend router (`slop_studio.backends.router`) between the MCP tool layer and `backends.local`, preparing the seam for a future cloud backend (Story 6.2)

### Fixed

- Normalised sanitisation of the `view()` image-URL handler and addressed deferred cleanup from the Stories 6.1/6.2 refactors (Story 6.2 followups)

## [0.3.1] - 2026-04-15

### Changed

- Extracted local ComfyUI backend from `slop_studio/comfyui.py` into `slop_studio/backends/local.py` behind a `Backend` interface (Story 6.1)

### Fixed

- Off-thread synchronous disk I/O and image decoding in the local backend (`_upload_image` verify, `get_image` write) so they no longer block the asyncio event loop

## [0.3.0] - 2026-04-08

### Removed

- `open_image` tool — replaced by `open_gallery` which now handles single images directly

### Changed

- `open_gallery` now accepts a single path or a list; single images open in the OS default viewer instead of generating an HTML gallery

### Added

- `open_gallery` tool for image viewing via OS viewer (single) or HTML gallery with lightbox (multiple)
- Claude Desktop integration: `desktop-config` CLI subcommand and setup docs
- MCPB Desktop Extension packaging
- Cross-platform process management abstraction
- Inline thumbnail previews in `get_image` responses
- Persistent config file and defensive tool handlers
- Interactive ComfyUI prompt during `init` with config persistence
- Idle timeout and automatic ComfyUI shutdown
- PID file tracking and orphan cleanup
- Lazy ComfyUI startup with health checks before use

### Fixed

- Ignore unresolved `${...}` placeholder env vars in config
- Add missing `resolution_steps` to `flux2_klein_edit` template
- Image extension allowlist for `open_gallery` tool
- Thumbnail size reduced to fit Claude Desktop tool result limit
- Auto-detect ComfyUI and populate `COMFYUI_START_CMD` in init

## [0.2.0] - 2026-04-06

### Added

- Auto-launch ComfyUI as a managed sidecar process
- Skip ComfyUI spawn when already running

### Fixed

- Kill entire process group on ComfyUI shutdown
- Use `/system_stats` for readiness polling instead of `/ready`

## [0.1.0] - 2026-04-05

### Added

- MCP server with ComfyUI workflow templates, job submission, monitoring, and image retrieval
- `slop-studio` CLI with `auth`, `init`, and `serve` subcommands
- `post_to_bluesky` tool for sharing images to Bluesky (supports up to 4 images per post)
- `check_next_job` tool for batch job polling
- `sloppify_prompt` tool for CLIP-based prompt synonymisation
- Image input support for Flux.2 Klein edit workflows
- Auto-load `.env` from project dir for credentials
- Workflow template validation
- Defensive hardening: config validation, injection guards, filename collision prevention
- CI build and test workflow
- MIT license

### Fixed

- Output and templates resolve to the art project dir, not the package dir

[Unreleased]: https://github.com/sathias/slop-studio/compare/v0.4.3...HEAD
[0.4.3]: https://github.com/sathias/slop-studio/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/sathias/slop-studio/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/sathias/slop-studio/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/sathias/slop-studio/compare/v0.3.6...v0.4.0
[0.3.1]: https://github.com/sathias/slop-studio/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/sathias/slop-studio/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/sathias/slop-studio/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/sathias/slop-studio/releases/tag/v0.1.0
