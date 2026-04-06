---
stepsCompleted:
  - "step-01-validate-prerequisites"
  - "step-02-design-epics"
  - "step-03-create-stories"
  - "step-04-final-validation"
status: "complete"
completedAt: "2026-04-06"
inputDocuments:
  - "research/technical-slop-studio-claude-desktop-integration-research-2026-04-06.md"
---

# ComfyClaude - Epic Breakdown (Desktop Integration)

## Overview

This document provides the complete epic and story breakdown for ComfyClaude's Claude Desktop integration, decomposing the implementation roadmap from the technical research document into implementable stories.

## Requirements Inventory

### Functional Requirements

FR1: The server defaults `OUTPUT_DIR` to `~/slop-studio/output` (absolute path) instead of `./output` (relative) so it works when launched from an undefined CWD
FR2: The server supports a `~/.config/slop-studio/config.toml` file for persistent user defaults (`output_dir`, `templates_dir`)
FR3: Path resolution follows a priority hierarchy: env var → `--project-dir` → config.toml → absolute default
FR4: All MCP tool handlers are wrapped in try/except so exceptions never propagate to the client
FR5: Tool handlers return human-readable error messages as text content on failure instead of crashing
FR6: Full tracebacks are logged to stderr for debugging while users see clean error messages
FR7: `get_image` returns a base64-encoded JPEG thumbnail (~512px, quality 80) as an inline `ImageContent` block
FR8: `get_image` always saves the full-resolution image to disk regardless of inline return
FR9: `get_image` returns both an `ImageContent` thumbnail and a `TextContent` block with the full-resolution file path
FR10: ComfyUI is spawned lazily on first `queue_prompt` call, not at MCP server startup
FR11: A background task monitors idle time and shuts down ComfyUI after a configurable timeout (default 15 minutes)
FR12: The server writes a PID file to `~/.config/slop-studio/comfyui.pid` on ComfyUI spawn and cleans it up on shutdown
FR13: On startup, the server checks for stale PID files and kills orphaned ComfyUI processes
FR14: Before each `queue_prompt`, the server health-checks ComfyUI and re-spawns if it has crashed

### NonFunctional Requirements

NFR1: Inline thumbnails must stay under 100KB / 25,000 tokens (512px JPEG q80 achieves this)
NFR2: ComfyUI idle timeout is configurable via `COMFYUI_IDLE_TIMEOUT` env var (default 900 seconds)
NFR3: Pillow is added as a new dependency for image resizing
NFR4: The server must never crash — a crashed MCP server kills the entire Claude Desktop session
NFR5: All changes must maintain backward compatibility with Claude Code (unified server, dual client)

### Additional Requirements

AR1: Package as a `.mcpb` Desktop Extension with `uv` server type for one-click installation
AR2: `manifest.json` declares `env_keys` for `COMFYUI_URL`, `COMFYUI_START_CMD`, `SLOP_STUDIO_OUTPUT_DIR`
AR3: Bundle default templates in the `.mcpb` package
AR4: Support manual `claude_desktop_config.json` setup as fallback to `.mcpb`
AR5: Windows process management (`os.killpg` is Unix-only) needs platform-specific alternatives
AR6: Explore MCP Task protocol for single-tool generation UX (Phase 4, experimental)

### UX Design Requirements

N/A — no UX design document (CLI/MCP server project)

### FR Coverage Map

FR1: Epic 1 - Absolute default OUTPUT_DIR (`~/slop-studio/output`)
FR2: Epic 1 - config.toml for persistent user defaults
FR3: Epic 1 - Path resolution priority hierarchy
FR4: Epic 1 - try/except on all tool handlers
FR5: Epic 1 - Human-readable error text on failure
FR6: Epic 1 - Full tracebacks logged to stderr
FR7: Epic 2 - Base64 thumbnail inline return
FR8: Epic 2 - Full-res always saved to disk
FR9: Epic 2 - Dual return: ImageContent + TextContent
FR10: Epic 3 - Lazy ComfyUI spawn on first use
FR11: Epic 3 - Idle timeout background task
FR12: Epic 3 - PID file tracking
FR13: Epic 3 - Orphan cleanup on startup
FR14: Epic 3 - Health-check before each queue_prompt
NFR1: Epic 2 - Thumbnail size constraints
NFR2: Epic 3 - Configurable idle timeout
NFR3: Epic 2 - Pillow dependency
NFR4: Epic 1 - Server must never crash
NFR5: All Epics - Backward compatibility with Claude Code
AR1: Epic 4 - MCPB packaging
AR2: Epic 4 - manifest.json with env_keys
AR3: Epic 4 - Bundle default templates
AR4: Epic 4 - Manual claude_desktop_config.json fallback
AR5: Epic 4 - Windows process management
AR6: Epic 4 - MCP Task protocol (experimental, deferred)

## Epic List

### Epic 1: Desktop-Ready Foundation
The developer can run slop-studio in Claude Desktop with correct output paths and without risk of the server crashing the entire Desktop session. This is the minimum viable change to make slop-studio functional in Claude Desktop.
**FRs covered:** FR1, FR2, FR3, FR4, FR5, FR6
**NFRs addressed:** NFR4, NFR5

### Epic 2: Inline Image Display
The developer sees generated images directly in the Claude Desktop conversation instead of just a file path string, while full-resolution images are always preserved on disk.
**FRs covered:** FR7, FR8, FR9
**NFRs addressed:** NFR1, NFR3

### Epic 3: Smart ComfyUI Lifecycle
The developer's GPU memory isn't consumed indefinitely during long Claude Desktop sessions. ComfyUI starts automatically when needed, shuts down when idle, and recovers gracefully from crashes or orphaned processes.
**FRs covered:** FR10, FR11, FR12, FR13, FR14
**NFRs addressed:** NFR2

### Epic 4: One-Click Desktop Installation
The developer can install slop-studio in Claude Desktop with a single click via a `.mcpb` Desktop Extension, with no manual JSON editing or `uv` setup required.
**FRs covered:** AR1, AR2, AR3, AR4
**Additional:** AR5 (Windows), AR6 (MCP Task protocol — experimental, deferred)

## Epic 1: Desktop-Ready Foundation

The developer can run slop-studio in Claude Desktop with correct output paths and without risk of the server crashing the entire Desktop session.

### Story 1.1: Absolute Default Output Path

As a developer using Claude Desktop,
I want slop-studio to default to `~/slop-studio/output` instead of `./output`,
So that my generated images land in a predictable location even when Claude Desktop launches the server from an undefined working directory.

**Acceptance Criteria:**

**Given** no `SLOP_STUDIO_OUTPUT_DIR` env var and no `--project-dir` argument are set
**When** the server starts
**Then** `OUTPUT_DIR` resolves to `~/slop-studio/output` (expanded to absolute path)

**Given** `SLOP_STUDIO_OUTPUT_DIR` is set to `/custom/path`
**When** the server starts
**Then** `OUTPUT_DIR` resolves to `/custom/path` (env var takes precedence) (FR1, FR3)

**Given** `--project-dir ~/my-art` is passed
**When** the server starts
**Then** `OUTPUT_DIR` resolves to `~/my-art/output` (project-dir takes precedence over default) (FR3)

**Given** the server is running in Claude Code with a `.mcp.json` using `--project-dir`
**When** images are generated
**Then** behavior is identical to current — no regression in Claude Code workflows (NFR5)

### Story 1.2: Persistent Config File

As a developer using Claude Desktop,
I want to set my preferred output and templates directories once in `~/.config/slop-studio/config.toml`,
So that I don't have to configure env vars every time and my preferences persist across sessions.

**Acceptance Criteria:**

**Given** `~/.config/slop-studio/config.toml` exists with `output_dir = "/my/images"` and `templates_dir = "/my/templates"`
**When** the server starts with no env vars or `--project-dir` set
**Then** the server uses the config.toml values (FR2)

**Given** both `config.toml` and `SLOP_STUDIO_OUTPUT_DIR` env var are set
**When** the server starts
**Then** the env var takes precedence over config.toml (FR3)

**Given** `config.toml` does not exist
**When** the server starts
**Then** the server falls through to the absolute default (`~/slop-studio/output`) with no error (FR3)

**Given** `config.toml` contains invalid TOML syntax
**When** the server starts
**Then** a warning is logged to stderr and the server falls through to defaults (does not crash) (NFR4)

**Given** the full priority hierarchy: env var → `--project-dir` → config.toml → absolute default
**When** any combination of these is set
**Then** the first match wins, in order (FR3)

### Story 1.3: Defensive Tool Handlers

As a developer using Claude Desktop,
I want all MCP tool handlers to catch and report errors gracefully,
So that a single tool failure never crashes the MCP server and kills my entire Desktop session.

**Acceptance Criteria:**

**Given** any MCP tool handler raises an unexpected exception
**When** the tool is called by Claude Desktop
**Then** the exception is caught, a human-readable error message is returned as text content, and the server continues running (FR4, FR5, NFR4)

**Given** an exception occurs in a tool handler
**When** the error is caught
**Then** the full traceback is logged to stderr for debugging (FR6)

**Given** an exception occurs in a tool handler
**When** the error response is returned
**Then** it is a plain text message that Claude can relay to the user (not a raw stack trace) (FR5)

**Given** multiple tools are called in sequence and one fails
**When** the next tool is called
**Then** it operates normally — a previous failure does not corrupt server state (NFR4)

**Given** the existing error handling (transient/terminal error helpers) is in place
**When** the defensive wrapper catches an untyped exception
**Then** it returns a generic error message without interfering with the existing structured error system

**Given** the server is running in Claude Code
**When** any tool handler is called
**Then** the defensive wrapping is transparent — behavior is identical to Claude Desktop (NFR5)

## Epic 2: Inline Image Display

The developer sees generated images directly in the Claude Desktop conversation instead of just a file path string, while full-resolution images are always preserved on disk.

### Story 2.1: Pillow Dependency & Thumbnail Generation

As a developer using slop-studio,
I want the server to generate compressed JPEG thumbnails from full-resolution images,
So that images can be returned inline without exceeding MCP size limits.

**Acceptance Criteria:**

**Given** `Pillow` is listed as a dependency in `pyproject.toml`
**When** `uv sync` is run
**Then** Pillow is installed and importable

**Given** a full-resolution image (e.g., 2048x2048 PNG, 10MB)
**When** the thumbnail generation function is called
**Then** it returns a base64-encoded JPEG string resized to fit within 512x512 pixels, preserving aspect ratio (FR7)

**Given** a full-resolution RGBA or palette-mode image
**When** the thumbnail generation function is called
**Then** it converts to RGB before saving as JPEG (no crash on transparency)

**Given** a 512x512 JPEG at quality 80
**When** the base64-encoded output is measured
**Then** it is under 100KB (well within the 1MB MCP limit and 25,000 token limit) (NFR1)

**Given** a small image that is already under 512px in both dimensions
**When** the thumbnail generation function is called
**Then** it does not upscale — returns the image at its original size, compressed as JPEG

**Given** the thumbnail generation function
**When** unit tests are run
**Then** all tests pass verifying size constraints, format conversion, and aspect ratio preservation

### Story 2.2: Hybrid Image Return in get_image

As a developer using Claude Desktop,
I want `get_image` to return both an inline thumbnail and the full-resolution file path,
So that I see a preview in the conversation while the full-quality image is always available on disk.

**Acceptance Criteria:**

**Given** a completed generation job
**When** `get_image` is called
**Then** it returns both an `ImageContent` block (base64 JPEG thumbnail) and a `TextContent` block with the full-resolution file path (FR9)

**Given** `get_image` retrieves an image from ComfyUI
**When** the image is processed
**Then** the full-resolution image is always saved to disk first, before any thumbnail generation (FR8)

**Given** the full-resolution image is saved to disk
**When** the thumbnail is generated
**Then** the thumbnail is created from the downloaded image bytes — the on-disk file is not re-read

**Given** thumbnail generation fails (e.g., corrupt image data)
**When** `get_image` processes the result
**Then** it falls back to returning only the `TextContent` file path (no crash, image still saved to disk) (FR8, NFR4)

**Given** the server is running in Claude Code
**When** `get_image` is called
**Then** Claude Code receives both the `ImageContent` and `TextContent` blocks — backward compatible, Claude Code can display the inline image too (NFR5)

**Given** the story is complete
**When** tests are run
**Then** all tests verify dual-return format, fallback behavior, and that full-res is always saved to disk

## Epic 3: Smart ComfyUI Lifecycle

The developer's GPU memory isn't consumed indefinitely during long Claude Desktop sessions. ComfyUI starts automatically when needed, shuts down when idle, and recovers gracefully from crashes or orphaned processes.

### Story 3.1: Lazy ComfyUI Start & Health Check Before Use

As a developer using Claude Desktop,
I want ComfyUI to start automatically on my first generation request instead of at server boot,
So that GPU memory isn't consumed until I actually need it, and ComfyUI is verified healthy before each job.

**Acceptance Criteria:**

**Given** the MCP server starts and `COMFYUI_START_CMD` is configured
**When** the lifespan context initializes
**Then** ComfyUI is NOT spawned — it remains idle until first use (FR10)

**Given** ComfyUI is not running
**When** `queue_prompt` is called for the first time
**Then** the server spawns ComfyUI, waits for it to become healthy (polling `/system_stats`), and then submits the job (FR10)

**Given** ComfyUI is already running and healthy
**When** `queue_prompt` is called
**Then** the server skips spawning and submits the job immediately

**Given** ComfyUI was running but has crashed since the last call
**When** `queue_prompt` is called
**Then** the health check detects the failure, cleans up the dead process, re-spawns ComfyUI, and submits the job transparently (FR14)

**Given** ComfyUI is already running externally (user started it manually)
**When** `queue_prompt` is called with no `COMFYUI_START_CMD` set
**Then** the health check passes against the external instance and the job proceeds (no spawn attempt)

**Given** ComfyUI fails to start (e.g., bad start command, port conflict)
**When** the spawn timeout is reached
**Then** a human-readable error is returned — the server does not crash (NFR4)

**Given** the server is running in Claude Code with the current start-at-boot behavior
**When** the server starts
**Then** lazy start works identically — ComfyUI spawns on first `queue_prompt` rather than at boot (NFR5)

### Story 3.2: PID File Tracking & Orphan Cleanup

As a developer using Claude Desktop,
I want the server to track ComfyUI's process ID and clean up orphans on startup,
So that a crashed Desktop session doesn't leave ComfyUI consuming GPU memory indefinitely.

**Acceptance Criteria:**

**Given** ComfyUI is spawned by the server
**When** the process starts successfully
**Then** its PID is written to `~/.config/slop-studio/comfyui.pid` (FR12)

**Given** the `~/.config/slop-studio/` directory does not exist
**When** the server needs to write the PID file
**Then** the directory is created automatically (with appropriate permissions)

**Given** ComfyUI is shut down gracefully (server exit, idle timeout)
**When** the shutdown completes
**Then** the PID file is removed (FR12)

**Given** a stale PID file exists from a previous crash (PID is still a running ComfyUI process)
**When** the MCP server starts
**Then** it kills the orphaned process group (SIGTERM → wait → SIGKILL) and removes the PID file (FR13)

**Given** a stale PID file exists but the process is already dead
**When** the MCP server starts
**Then** it removes the stale PID file without error (FR13)

**Given** a stale PID file exists but the PID now belongs to a different process (PID reuse)
**When** the MCP server starts
**Then** it does NOT kill the unrelated process — it removes the stale PID file only

**Given** the story is complete
**When** tests are run
**Then** all PID file write/read/cleanup scenarios pass, including orphan detection and PID reuse safety

### Story 3.3: Idle Timeout & Automatic Shutdown

As a developer in a long Claude Desktop session,
I want ComfyUI to shut down automatically after I stop generating images for a while,
So that my GPU memory is freed without me having to remember to stop it manually.

**Acceptance Criteria:**

**Given** ComfyUI is running and no generation has been requested for 15 minutes (default)
**When** the idle watcher background task checks activity
**Then** it gracefully shuts down ComfyUI (SIGTERM → grace period → SIGKILL) and removes the PID file (FR11)

**Given** `COMFYUI_IDLE_TIMEOUT` env var is set to `300` (5 minutes)
**When** the idle watcher is configured
**Then** it uses the custom timeout value instead of the default (NFR2)

**Given** ComfyUI was shut down by idle timeout
**When** the next `queue_prompt` is called
**Then** ComfyUI is re-spawned transparently via lazy start (Story 3.1) — the developer sees no difference

**Given** the idle watcher is running and a new generation is requested
**When** the generation completes
**Then** the idle timer resets — the timeout window starts fresh from the last activity

**Given** `COMFYUI_IDLE_TIMEOUT` is set to `0`
**When** the server starts
**Then** the idle watcher is disabled — ComfyUI stays running until server exit (opt-out for power users)

**Given** the idle watcher background task
**When** the MCP server shuts down
**Then** the background task is cancelled cleanly without errors

**Given** the story is complete
**When** tests are run
**Then** all idle timeout scenarios pass, including custom values, timer reset, opt-out, and clean cancellation

## Epic 4: One-Click Desktop Installation

The developer can install slop-studio in Claude Desktop with a single click via a `.mcpb` Desktop Extension, with no manual JSON editing or `uv` setup required.

### Story 4.1: Manual Claude Desktop Configuration

As a developer who wants to use slop-studio in Claude Desktop today,
I want clear documentation and a working `claude_desktop_config.json` snippet,
So that I can set up slop-studio manually before the one-click installer is available.

**Acceptance Criteria:**

**Given** the README or a dedicated Desktop setup guide
**When** I follow the manual setup instructions
**Then** I can add slop-studio to `~/Library/Application Support/Claude/claude_desktop_config.json` with the correct `command`, `args`, and `env` block (AR4)

**Given** the documented config snippet
**When** I paste it into `claude_desktop_config.json`
**Then** it includes `COMFYUI_URL`, `COMFYUI_START_CMD`, and `SLOP_STUDIO_OUTPUT_DIR` in the `env` block with sensible placeholder values (AR2)

**Given** the manual configuration is in place
**When** I restart Claude Desktop
**Then** slop-studio tools appear and image generation works end-to-end

**Given** a developer is using Claude Code with `.mcp.json`
**When** they read the Desktop setup docs
**Then** the docs clearly explain the difference between Code (`.mcp.json`, per-project) and Desktop (`claude_desktop_config.json`, global) configuration

**Given** the story is complete
**When** the documentation is reviewed
**Then** it covers: prerequisites (ComfyUI installed, Python 3.11+), the config snippet, env var explanations, and a troubleshooting section for common issues

### Story 4.2: MCPB Desktop Extension Package

As a developer who wants the simplest possible setup,
I want to install slop-studio by double-clicking a `.mcpb` file,
So that Claude Desktop configures everything automatically without me editing JSON files or installing `uv`.

**Acceptance Criteria:**

**Given** the slop-studio repository
**When** the MCPB build process runs
**Then** it produces a `slop-studio-{version}.mcpb` file (zip archive) containing `manifest.json`, `pyproject.toml`, source code, and bundled templates (AR1)

**Given** the `manifest.json` in the package
**When** it is inspected
**Then** it declares `server.type` as `uv`, the correct `entry_point`, and `env_keys` for `COMFYUI_URL`, `COMFYUI_START_CMD`, and `SLOP_STUDIO_OUTPUT_DIR` (AR2)

**Given** the `.mcpb` package includes templates
**When** the package contents are inspected
**Then** all default templates (`.json` + `.meta.json` pairs) are bundled (AR3)

**Given** a developer double-clicks the `.mcpb` file
**When** Claude Desktop installs the extension
**Then** it prompts for the required `env_keys` values and configures the server automatically

**Given** the extension is installed
**When** Claude Desktop starts
**Then** slop-studio tools are available and generation works with the bundled templates

**Given** the `.mcpb` build process
**When** integrated into CI/CD (GitHub Actions)
**Then** tagged releases automatically produce a `.mcpb` artifact attached to the GitHub release

### Story 4.3: Cross-Platform Process Management

As a developer running slop-studio on Windows,
I want ComfyUI process management to work correctly on my platform,
So that lifecycle features (lazy start, PID tracking, orphan cleanup, idle timeout) aren't broken by Unix-only assumptions.

**Acceptance Criteria:**

**Given** the current ComfyUI shutdown logic uses `os.killpg()` (Unix-only)
**When** the server runs on Windows
**Then** it uses a platform-appropriate alternative (e.g., `taskkill /T /F /PID` or `psutil` process tree kill) (AR5)

**Given** the current spawn logic uses `start_new_session=True` for process groups
**When** the server runs on Windows
**Then** it uses `CREATE_NEW_PROCESS_GROUP` flag or equivalent for subprocess isolation (AR5)

**Given** PID file tracking (Story 3.2)
**When** orphan cleanup runs on Windows
**Then** it correctly detects whether the PID is still alive and terminates it using platform-appropriate signals

**Given** the platform-specific code paths
**When** the server runs on macOS or Linux
**Then** existing Unix behavior is unchanged — no regressions (NFR5)

**Given** the story is complete
**When** tests are run
**Then** platform-detection logic is tested, and platform-specific code paths have unit tests (mocked OS calls where needed)
