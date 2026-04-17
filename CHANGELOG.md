# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/sathias/slop-studio/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/sathias/slop-studio/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/sathias/slop-studio/compare/v0.3.6...v0.4.0
[0.3.1]: https://github.com/sathias/slop-studio/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/sathias/slop-studio/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/sathias/slop-studio/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/sathias/slop-studio/releases/tag/v0.1.0
