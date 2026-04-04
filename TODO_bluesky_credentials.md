# TODO: Bluesky Credentials Onboarding

When a user sets up a new project directory, `post_to_bluesky` fails with a confusing missing-env-var error because `BSKY_HANDLE` and `BSKY_APP_PASSWORD` aren't configured in the project's `.mcp.json`.

## Problem

Credentials are per-project (stored in each `.mcp.json` `env` block), so every new project folder starts with no Bluesky auth. There's no onboarding flow or way to inherit credentials from a previous setup.

## Options

1. **Onboarding flow** — When `post_to_bluesky` is called without credentials, prompt the user to enter their handle and app password, then offer to write them into `.mcp.json` automatically.
2. **Shared credentials file** — Read from a central `~/.config/slop-studio/bluesky.json` (or similar) as a fallback when env vars aren't set in `.mcp.json`. This way credentials are configured once and work across all project directories.
3. **Both** — Use a shared config as the default, but allow per-project overrides via `.mcp.json` env vars.

## Security considerations

- App passwords (not main passwords) should always be used.
- Credentials should not be written into files that get committed to git. If using option 1, ensure `.mcp.json` is in `.gitignore` or only write to the `env` block which is already local.
- A shared config file should have restricted permissions (600).
