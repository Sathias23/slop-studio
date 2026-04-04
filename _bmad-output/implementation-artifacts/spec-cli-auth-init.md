---
title: 'CLI entry point with auth and init subcommands'
type: 'feature'
created: '2026-04-05'
status: 'done'
baseline_commit: '87443f0'
context: [TODO_bluesky_credentials.md]
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** slop-studio has no proper CLI entry point. `init` is invoked via `uv run main.py init` through a zsh alias, and Bluesky credentials must be manually configured per-project in `.mcp.json` env blocks or `.env` files — there's no central auth flow.

**Approach:** Add an `argparse`-based CLI (`slop-studio`) with three subcommands: `auth` (store Bluesky credentials centrally at `~/.config/slop-studio/credentials.json`), `init` (scaffold art project, now generating `.mcp.json` pointing at `slop-studio serve`), and `serve` (launch MCP server). Make credential loading lazy with a 3-tier fallback: env var → project `.env` → central config file.

## Boundaries & Constraints

**Always:**
- Use `argparse` only — zero new dependencies
- Credentials file at `~/.config/slop-studio/credentials.json` with mode 0600
- Namespace credentials under `"bluesky"` key for future extensibility
- Credential precedence: env vars → project `.env` (existing) → central config file
- `getpass` for app password input (masked)
- Confirm overwrite if credentials file already exists

**Ask First:**
- Adding subcommands beyond auth/init/serve

**Never:**
- Keychain/keyring integration
- New pip dependencies
- Breaking existing `.env`-based credential flow

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| `auth` happy path | Valid handle + app password prompted | Writes `~/.config/slop-studio/credentials.json` (0600), prints confirmation | N/A |
| `auth` overwrite | Credentials file exists | Prompts "Overwrite? [Y/n]", replaces on Y, aborts on n | N/A |
| `init` happy path | Target directory (default cwd) | Scaffolds project, `.mcp.json` uses `slop-studio serve` | N/A |
| `serve` | No args | Launches MCP server (same as current default) | N/A |
| No subcommand | `slop-studio` with no args | Shows help listing auth, init, serve | N/A |
| Credential resolution — env var | `BSKY_HANDLE` set in env | Env var wins over file | N/A |
| Credential resolution — central file | No env var, file exists | Reads from `~/.config/slop-studio/credentials.json` | N/A |
| Credential resolution — nothing | No env var, no file | `post_to_bluesky` returns error: "Run: slop-studio auth" | terminal_error |

</frozen-after-approval>

## Code Map

- `slop_studio/cli.py` -- NEW: argparse CLI with auth/init/serve subcommands
- `pyproject.toml` -- Add `[project.scripts]` entry point
- `slop_studio/config.py` -- Replace BSKY constants with lazy `get_bsky_credentials()` + central file fallback
- `slop_studio/bluesky.py` -- Use `get_bsky_credentials()`, update error message
- `slop_studio/init.py` -- Update `.mcp.json` generation to use `slop-studio serve`
- `main.py` -- Thin shim delegating to `cli.main()` for backwards compat
- `tests/test_cli.py` -- NEW: test auth write, overwrite prompt, init delegation
- `tests/test_bluesky.py` -- Update credential-missing tests for new error message
- `tests/test_init.py` -- Update `.mcp.json` assertions for `slop-studio serve`

## Tasks & Acceptance

**Execution:**
- [x] `slop_studio/cli.py` -- Create argparse CLI with auth, init, serve subcommands -- core entry point
- [x] `pyproject.toml` -- Add `[project.scripts]` slop-studio = "slop_studio.cli:main" -- make CLI installable
- [x] `slop_studio/config.py` -- Replace module-level BSKY constants with `get_bsky_credentials()` function that checks env → central file -- lazy loading + fallback chain
- [x] `slop_studio/bluesky.py` -- Import and call `get_bsky_credentials()`, update missing-config error to say "Run: slop-studio auth" -- better UX
- [x] `slop_studio/init.py` -- Change `.mcp.json` command from `uv run main.py` to `slop-studio serve`, keep `--project-dir` -- cleaner for installed users
- [x] `main.py` -- Delegate to `cli.main()` for backwards compat -- existing `.mcp.json` files keep working
- [x] `tests/test_cli.py` -- Test auth flow (write, overwrite, abort), credential file permissions -- cover new code
- [x] `tests/test_bluesky.py` -- Update assertions for new error message wording -- keep tests passing
- [x] `tests/test_init.py` -- Update `.mcp.json` assertions to expect `slop-studio serve` -- keep tests passing

**Acceptance Criteria:**
- Given slop-studio is pip-installed, when user runs `slop-studio auth`, then they are prompted for handle and app password and credentials are saved to `~/.config/slop-studio/credentials.json` with 0600 permissions
- Given credentials exist in central file but not env, when `post_to_bluesky` is called, then credentials are loaded from the central file
- Given env vars are set, when `post_to_bluesky` is called, then env vars take precedence over the central file
- Given no credentials anywhere, when `post_to_bluesky` is called, then error message says "Run: slop-studio auth"
- Given slop-studio is installed, when user runs `slop-studio init`, then project is scaffolded with `.mcp.json` pointing at `slop-studio serve`
- Given slop-studio is installed, when user runs `slop-studio` with no args, then help is displayed

## Verification

**Commands:**
- `pytest tests/test_cli.py tests/test_bluesky.py tests/test_init.py -v` -- expected: all pass
- `pip install -e . && slop-studio --help` -- expected: shows auth, init, serve subcommands

## Suggested Review Order

**CLI entry point and auth flow**

- argparse CLI with auth/init/serve — the core new file, start here
  [`cli.py:66`](../../slop_studio/cli.py#L66)

- Atomic credential write with os.open for 0600 permissions from the start
  [`cli.py:35`](../../slop_studio/cli.py#L35)

- Overwrite prompt with correct [Y/n] default-yes semantics
  [`cli.py:17`](../../slop_studio/cli.py#L17)

**Credential resolution chain**

- Lazy 3-tier fallback replaces module-level constants
  [`config.py:25`](../../slop_studio/config.py#L25)

- Bluesky uses lazy credentials, error now says "Run: slop-studio auth"
  [`bluesky.py:43`](../../slop_studio/bluesky.py#L43)

**Init and backwards compat**

- .mcp.json now generates `slop-studio serve` instead of `uv run main.py`
  [`init.py:27`](../../slop_studio/init.py#L27)

- main.py shim rewrites argv and delegates to cli.main() for old configs
  [`main.py:26`](../../main.py#L26)

- Entry point registration
  [`pyproject.toml:21`](../../pyproject.toml#L21)

**Tests**

- New CLI tests: auth save, permissions, overwrite, empty input, init delegation
  [`test_cli.py:22`](../../tests/test_cli.py#L22)

- Bluesky tests patching updated from constants to get_bsky_credentials
  [`test_bluesky.py:53`](../../tests/test_bluesky.py#L53)

- Init tests updated for slop-studio serve assertions
  [`test_init.py:37`](../../tests/test_init.py#L37)
