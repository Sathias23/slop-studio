# Manual Testing Guide: Cross-Platform Desktop Integration

**Scope:** Epic 4 (One-Click Desktop Installation) + full regression across macOS and Windows.
**Stories covered:** 4.1 (Desktop Config CLI), 4.2 (MCPB Packaging), 4.3 (Cross-Platform Process Management), plus regressions for Epics 1-3.

---

## Prerequisites

### Both Platforms

- ComfyUI installed and working (can generate images when started manually)
- Claude Desktop installed (latest version)
- Claude Code installed (latest version)
- Python >= 3.11
- `uv` package manager installed
- A Bluesky account with an app password (optional, for posting tests)

### macOS Specifics

- Verify `pbcopy` available (for `--copy` clipboard test)
- Note config path: `~/Library/Application Support/Claude/claude_desktop_config.json`

### Windows Specifics

- Verify `taskkill`, `tasklist`, `wmic` available in PATH (run each with `/?`)
- Note config path: `%APPDATA%\Claude\claude_desktop_config.json`
- PowerShell or cmd terminal

### Test Environment Setup

```bash
# Clone and install
git clone <repo-url> && cd slop-studio
uv sync

# Verify installation
slop-studio --help
```

---

## Part 1: CLI Commands (Both Platforms)

### 1.1 `slop-studio init`

| # | Test | Steps | Expected | macOS | Windows |
|---|------|-------|----------|-------|---------|
| 1.1.1 | Init creates project scaffold | `mkdir /tmp/test-art && slop-studio init /tmp/test-art` | Creates `templates/` (3 templates), `.mcp.json`, `.claude/commands/generate.md`, `CLAUDE.md` | | |
| 1.1.2 | Init auto-detects ComfyUI | With ComfyUI at `~/ComfyUI`, run `slop-studio init /tmp/test-art2` | `.mcp.json` contains correct `COMFYUI_START_CMD` pointing to detected path | | |
| 1.1.3 | Init without ComfyUI | Rename/hide ComfyUI, run init | Falls back to `python ~/ComfyUI/main.py`, no crash | | |
| 1.1.4 | Init on existing directory | Run init twice on same dir | Second run completes without error (overwrites or skips gracefully) | | |

### 1.2 `slop-studio desktop-config`

| # | Test | Steps | Expected | macOS | Windows |
|---|------|-------|----------|-------|---------|
| 1.2.1 | Config snippet output | `slop-studio desktop-config` | Prints valid JSON with `mcpServers.slop-studio` containing `command`, `args: ["serve"]`, and `env` block | | |
| 1.2.2 | Auto-detects binary | Check `command` field in output | Points to actual `slop-studio` binary (or `uv tool run slop-studio` fallback) | | |
| 1.2.3 | Auto-detects ComfyUI | Check `COMFYUI_START_CMD` in output | Contains detected ComfyUI path, or empty string if not found | | |
| 1.2.4 | Copy to clipboard | `slop-studio desktop-config --copy` | JSON copied to system clipboard; paste into text editor to verify | | |
| 1.2.5 | Output is valid for Desktop | Paste output into `claude_desktop_config.json` at correct OS path | Claude Desktop recognizes the server on restart | | |

### 1.3 `slop-studio build-mcpb`

| # | Test | Steps | Expected | macOS | Windows |
|---|------|-------|----------|-------|---------|
| 1.3.1 | Build succeeds | `slop-studio build-mcpb` | Creates `slop-studio-{version}.mcpb` in current directory | | |
| 1.3.2 | Custom output dir | `slop-studio build-mcpb --output-dir /tmp` | `.mcpb` created in `/tmp` | | |
| 1.3.3 | Package contents valid | Rename `.mcpb` to `.zip`, extract | Contains `manifest.json`, `pyproject.toml`, `slop_studio/` (no `mcpb.py`, no `__pycache__`), `templates/` | | |
| 1.3.4 | Version consistency | Check `manifest.json` version vs `pyproject.toml` version | Versions match | | |
| 1.3.5 | No process.py regression | Verify `slop_studio/process.py` is included in the package | File present in archive | | |

### 1.4 `slop-studio auth`

| # | Test | Steps | Expected | macOS | Windows |
|---|------|-------|----------|-------|---------|
| 1.4.1 | Fresh auth setup | `slop-studio auth`, enter handle + app password | Creates `~/.config/slop-studio/credentials.json` with mode 0600 | | |
| 1.4.2 | Overwrite existing | Run auth again | Prompts for confirmation, overwrites on accept | | |

### 1.5 `slop-studio serve`

| # | Test | Steps | Expected | macOS | Windows |
|---|------|-------|----------|-------|---------|
| 1.5.1 | Serve starts | `slop-studio serve` | Process starts, listens on stdio (no crash) | | |
| 1.5.2 | Serve with project-dir | `slop-studio serve --project-dir /tmp/test-art` | Uses project's `templates/` and `output/` dirs | | |
| 1.5.3 | Serve loads .env | Create `/tmp/test-art/.env` with `COMFYUI_URL=http://localhost:9999`, serve with `--project-dir` | Manager uses custom URL | | |

---

## Part 2: Claude Desktop Integration

### 2.1 Manual Setup (Story 4.1)

| # | Test | Steps | Expected | macOS | Windows |
|---|------|-------|----------|-------|---------|
| 2.1.1 | Manual config install | Run `slop-studio desktop-config`, paste JSON into `claude_desktop_config.json` | After restart, "slop-studio" appears in Claude Desktop's MCP server list | | |
| 2.1.2 | Tools visible | Open new conversation in Claude Desktop | `list_templates`, `queue_prompt`, etc. tools are available | | |
| 2.1.3 | Config path correct | Verify config file at OS-specific path | macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`, Windows: `%APPDATA%\Claude\claude_desktop_config.json` | | |

### 2.2 MCPB Desktop Extension (Story 4.2)

| # | Test | Steps | Expected | macOS | Windows |
|---|------|-------|----------|-------|---------|
| 2.2.1 | One-click install | Double-click `.mcpb` file | Claude Desktop opens install dialog | | |
| 2.2.2 | Env var prompts | During install | Prompted for `COMFYUI_URL`, `COMFYUI_START_CMD`, `SLOP_STUDIO_OUTPUT_DIR` with sensible defaults | | |
| 2.2.3 | Post-install tools | Restart Claude Desktop, open conversation | All slop-studio tools available | | |
| 2.2.4 | Bundled templates | Call `list_templates` | Returns 3 starter templates (flux2_klein, flux2_klein_ultrawide, flux2_klein_edit) | | |

---

## Part 3: Process Management (Story 4.3) — Critical Platform Tests

### 3.1 Lazy Start (ComfyUI Spawn)

| # | Test | Steps | Expected | macOS | Windows |
|---|------|-------|----------|-------|---------|
| 3.1.1 | ComfyUI not running at boot | Start slop-studio (via Desktop or CLI), check system processes | ComfyUI is NOT running after server starts | | |
| 3.1.2 | First queue_prompt spawns ComfyUI | Ask Claude to generate an image | ComfyUI spawns, health check succeeds, image generates | | |
| 3.1.3 | PID file created | After spawn, check `~/.config/slop-studio/comfyui.pid` | File exists, contains correct PID matching running ComfyUI process | | |
| 3.1.4 | Spawn with custom timeout | Set `COMFYUI_START_TIMEOUT=10`, generate image | ComfyUI starts within 10s (or returns timeout error if too slow) | | |
| 3.1.5 | Spawn failure (bad command) | Set `COMFYUI_START_CMD=nonexistent_binary`, generate | Returns transient error "Failed to start ComfyUI", server stays alive | | |

### 3.2 Process Isolation

| # | Test | Steps | Expected | macOS | Windows |
|---|------|-------|----------|-------|---------|
| 3.2.1 | Process group isolation (macOS) | After spawn, check `ps -o pgid -p <pid>` | ComfyUI runs in its own process group (PGID != parent PGID) | | N/A |
| 3.2.2 | Process group isolation (Windows) | After spawn, check Task Manager process tree | ComfyUI runs as a separate process tree | N/A | |
| 3.2.3 | Child processes included | If ComfyUI spawns child workers, verify they share the group/tree | Children visible under same group (macOS: `ps -g <pgid>`) or tree (Windows: Task Manager) | | |

### 3.3 Graceful Shutdown

| # | Test | Steps | Expected | macOS | Windows |
|---|------|-------|----------|-------|---------|
| 3.3.1 | Shutdown on server exit | Close Claude Desktop or Ctrl+C the server | ComfyUI process stops within ~10s; PID file removed | | |
| 3.3.2 | Shutdown kills children | ComfyUI has child processes, then shutdown | All child processes also terminated | | |
| 3.3.3 | PID file cleanup | After shutdown, check `~/.config/slop-studio/comfyui.pid` | File does not exist | | |
| 3.3.4 | Atexit safety net | Kill slop-studio with `kill -9` (macOS) or `taskkill /F` (Windows) | ComfyUI still gets killed via atexit handler (may not work for SIGKILL — verify) | | |

### 3.4 Idle Timeout

| # | Test | Steps | Expected | macOS | Windows |
|---|------|-------|----------|-------|---------|
| 3.4.1 | Default idle shutdown | Generate an image, then wait 15 minutes | ComfyUI shuts down automatically; log message "idle for Xs" appears | | |
| 3.4.2 | Custom timeout | Set `COMFYUI_IDLE_TIMEOUT=60`, generate, wait 60s | ComfyUI shuts down after ~60s of inactivity | | |
| 3.4.3 | Disable idle timeout | Set `COMFYUI_IDLE_TIMEOUT=0`, generate, wait | ComfyUI stays running indefinitely | | |
| 3.4.4 | Activity resets timer | Generate image, wait 10 min, generate another, wait 15 min | Shutdown happens 15 min after the SECOND generation, not the first | | |
| 3.4.5 | Transparent re-spawn | After idle shutdown, generate another image | ComfyUI re-spawns transparently; user sees no error | | |
| 3.4.6 | Negative timeout rejected | Set `COMFYUI_IDLE_TIMEOUT=-1`, start server | ValueError raised: "must be >= 0" | | |

### 3.5 Orphan Cleanup

| # | Test | Steps | Expected | macOS | Windows |
|---|------|-------|----------|-------|---------|
| 3.5.1 | Stale PID — dead process | Create `~/.config/slop-studio/comfyui.pid` with a non-existent PID (e.g., `999999`), start server | PID file removed, no error, server starts normally | | |
| 3.5.2 | Stale PID — live ComfyUI | Start ComfyUI manually, write its PID to the file, then start server | Orphaned ComfyUI killed, PID file removed | | |
| 3.5.3 | PID reuse safety | Write PID of a running non-ComfyUI process (e.g., your shell) to PID file, start server | Process NOT killed; PID file removed; warning logged about "not ComfyUI" | | |
| 3.5.4 | Invalid PID file | Write "garbage-text" to PID file, start server | PID file removed, warning logged, server starts | | |
| 3.5.5 | Permission error (macOS/Linux) | Write PID of a root-owned process, start as non-root | PID file removed, process not killed | | N/A |

### 3.6 Crash Recovery

| # | Test | Steps | Expected | macOS | Windows |
|---|------|-------|----------|-------|---------|
| 3.6.1 | ComfyUI crash mid-generation | Kill ComfyUI process manually during image generation | Error returned to user; next `queue_prompt` re-spawns ComfyUI | | |
| 3.6.2 | ComfyUI crash between requests | Kill ComfyUI, then request another generation | `ensure_ready` detects dead process, re-spawns, generation succeeds | | |
| 3.6.3 | Hung ComfyUI (unresponsive) | Make ComfyUI unresponsive (e.g., suspend with SIGSTOP on macOS), then generate | `ensure_ready` detects unresponsive, kills, re-spawns | | |

---

## Part 4: Image Generation Lifecycle (Regression)

### 4.1 Template Operations

| # | Test | Steps | Expected | macOS | Windows |
|---|------|-------|----------|-------|---------|
| 4.1.1 | List templates | Call `list_templates` | Returns all available templates with name, model, description | | |
| 4.1.2 | Get template details | Call `get_template("flux2_klein")` | Returns full metadata: inputs, aspect_ratios, resolution_nodes | | |
| 4.1.3 | Add custom template | Export workflow from ComfyUI, call `add_template` | Template appears in `list_templates` and is usable | | |
| 4.1.4 | Update template | Call `update_template` with modified metadata | Changes reflected in `get_template` | | |
| 4.1.5 | Delete template | Call `delete_template` | Template no longer in `list_templates` | | |
| 4.1.6 | Bad template name | Call with `../evil` or `.hidden` | Returns terminal error, no path traversal | | |

### 4.2 Image Generation

| # | Test | Steps | Expected | macOS | Windows |
|---|------|-------|----------|-------|---------|
| 4.2.1 | Basic generation | `queue_prompt` with "a sunset over mountains" | Returns `prompt_id`, job completes | | |
| 4.2.2 | Custom aspect ratio | `queue_prompt` with `aspect_ratio: "16:9"` | Image generated at 16:9 dimensions | | |
| 4.2.3 | Check job status | `check_next_job` with prompt_id, `wait: 30` | Returns completed status with outputs | | |
| 4.2.4 | Get image with thumbnail | `get_image` with completed prompt_id | Returns inline JPEG thumbnail + file path; file saved to `OUTPUT_DIR/YYYY-MM-DD/` | | |
| 4.2.5 | Batch job polling | Queue 3 prompts, call `check_next_job` with all IDs | All jobs tracked, completed ones returned as they finish | | |
| 4.2.6 | Missing required input | `queue_prompt` without required "prompt" input | Returns terminal error `invalid_inputs` | | |
| 4.2.7 | Invalid template name | `queue_prompt` with nonexistent template | Returns terminal error | | |

### 4.3 Output Directory

| # | Test | Steps | Expected | macOS | Windows |
|---|------|-------|----------|-------|---------|
| 4.3.1 | Default output path | Generate image without setting `SLOP_STUDIO_OUTPUT_DIR` | Saved to `~/slop-studio/output/YYYY-MM-DD/` | | |
| 4.3.2 | Custom output path | Set `SLOP_STUDIO_OUTPUT_DIR=/tmp/my-output`, generate | Saved to `/tmp/my-output/YYYY-MM-DD/` (macOS) or `C:\tmp\my-output\YYYY-MM-DD\` (Windows) | | |
| 4.3.3 | Filename dedup | Generate two images with same seed/params | Second file gets `_001` suffix, no overwrite | | |
| 4.3.4 | Absolute path required (Desktop) | In Desktop config, use relative path for output | Verify behavior (should resolve relative to server CWD or error) | | |

---

## Part 5: Configuration Priority (Regression)

| # | Test | Steps | Expected | macOS | Windows |
|---|------|-------|----------|-------|---------|
| 5.1 | Env var overrides config.toml | Set `SLOP_STUDIO_OUTPUT_DIR` env var AND different value in `config.toml` | Env var value used | | |
| 5.2 | config.toml overrides default | Set only `config.toml` value, no env var | config.toml value used | | |
| 5.3 | Default when nothing set | Remove env var and config.toml entry | Default `~/slop-studio/output` used | | |
| 5.4 | Invalid COMFYUI_URL | Set `COMFYUI_URL=ftp://bad` | Validation error on startup (must be http/https) | | |
| 5.5 | Invalid config.toml | Write invalid TOML to `~/.config/slop-studio/config.toml` | Warning logged, defaults used, no crash | | |

---

## Part 6: Error Resilience (Regression)

| # | Test | Steps | Expected | macOS | Windows |
|---|------|-------|----------|-------|---------|
| 6.1 | Tool error doesn't crash server | Trigger any tool error (bad inputs, network failure) | Error response returned; subsequent tool calls still work | | |
| 6.2 | ComfyUI unreachable, no start_cmd | Unset `COMFYUI_START_CMD`, ComfyUI not running | Transient error with `retry_suggested: true` and helpful message | | |
| 6.3 | Concurrent requests | Send multiple `queue_prompt` calls simultaneously | Lock prevents race conditions; all requests handled | | |
| 6.4 | PID file write failure | Make `~/.config/slop-studio/` read-only, generate | Warning logged, spawn still succeeds (PID tracking degraded) | | |

---

## Part 7: Bluesky Posting (Regression)

| # | Test | Steps | Expected | macOS | Windows |
|---|------|-------|----------|-------|---------|
| 7.1 | Single image post | Generate image, post with text and tags | Post appears on Bluesky with image and clickable hashtags | | |
| 7.2 | Multi-image post | Generate 2-4 images, post together | All images attached, up to 4 | | |
| 7.3 | Large image compression | Post an image > 1MB | Auto-compressed to JPEG, upload succeeds | | |
| 7.4 | No credentials | Remove BSKY_HANDLE/BSKY_APP_PASSWORD | Returns error with clear message about missing credentials | | |
| 7.5 | Env vars override credentials.json | Set env vars AND have credentials.json with different values | Env var credentials used | | |

---

## Part 8: Claude Code Regression (Both Platforms)

| # | Test | Steps | Expected | macOS | Windows |
|---|------|-------|----------|-------|---------|
| 8.1 | Init + Code workflow | `slop-studio init`, open project in Claude Code | MCP server auto-starts; `/generate` command works | | |
| 8.2 | Per-project templates | Add template in project A, verify not visible in project B | Templates scoped to project | | |
| 8.3 | .env loading | Create `.env` with custom config in project, use `--project-dir` | Config loaded from `.env` | | |
| 8.4 | Full generation cycle | `/generate a cat wearing a hat, cinematic lighting` | Image generated, thumbnail displayed inline, file saved | | |

---

## Quick Smoke Test (5-Minute Version)

For a fast sanity check on each platform:

1. **Install:** `uv tool install slop-studio` (or `pip install .`)
2. **Config:** `slop-studio desktop-config` -- verify JSON output looks correct
3. **Desktop setup:** Paste config into `claude_desktop_config.json`, restart Claude Desktop
4. **Verify tools:** Open conversation, confirm tools are listed
5. **Generate:** Ask Claude to "generate a sunset over mountains"
6. **Verify:** Image appears inline; check output directory for saved file
7. **Shutdown:** Close Claude Desktop; verify ComfyUI process stops (check with `ps aux | grep comfyui` on macOS or Task Manager on Windows)
8. **Orphan test:** Note ComfyUI PID, kill Claude Desktop with `kill -9` / `taskkill /F`, restart -- verify orphan cleaned up

---

## Platform Comparison Checklist

After completing all tests on both platforms, verify these platform-specific behaviors match expectations:

| Behavior | macOS Expected | Windows Expected |
|----------|---------------|-----------------|
| Process spawn | `start_new_session=True` | `CREATE_NEW_PROCESS_GROUP` flag |
| Process kill | `os.killpg(pgid, SIGKILL)` | `taskkill /T /F /PID` |
| Graceful shutdown | SIGTERM to process group | `taskkill /T /PID` (graceful tree kill) |
| Process alive check | `os.kill(pid, 0)` | `tasklist /FI "PID eq"` |
| Cmdline inspection | `ps -p <pid> -o command=` | `wmic process where ProcessId=... get CommandLine` |
| PID file location | `~/.config/slop-studio/comfyui.pid` | `~/.config/slop-studio/comfyui.pid` (via `Path.home()`) |
| Config file | `~/Library/Application Support/Claude/claude_desktop_config.json` | `%APPDATA%\Claude\claude_desktop_config.json` |
| Clipboard copy | `pbcopy` | `clip` |

---

## Reporting Issues

For each failure, record:
- **Platform + OS version** (e.g., macOS 15.2, Windows 11 23H2)
- **Test ID** from this guide (e.g., 3.1.2)
- **Steps to reproduce** (exact commands)
- **Expected vs actual behavior**
- **Logs** (`slop-studio serve 2>server.log` to capture stderr)
- **PID file state** (`cat ~/.config/slop-studio/comfyui.pid`)
- **Process state** (`ps aux | grep comfyui` or `tasklist /FI "IMAGENAME eq python.exe"`)
