# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `open_image` tool to launch images in the OS default viewer
- `open_gallery` tool for batch image viewing via an HTML gallery with lightbox
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
- Image extension allowlist for `open_image` tool
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

[Unreleased]: https://github.com/sathias/slop-studio/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/sathias/slop-studio/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/sathias/slop-studio/releases/tag/v0.1.0
