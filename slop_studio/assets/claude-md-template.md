# Art Project

This project uses [slop-studio](https://github.com/sathias/slop-studio) for conversational image generation via ComfyUI.

## Quick Start

Use `/generate <description>` to create an image. Example: `/generate a sunset over mountains, cinematic lighting`

## Available Tools

- `list_templates` — browse available workflow templates
- `get_template` — inspect inputs and aspect ratios for a template
- `queue_prompt` — submit a generation job
- `check_next_job` — poll multiple jobs for completion (use `wait: 30`)
- `get_image` — retrieve the output image path and base64 thumbnail preview
- `open_image` — open an image in the OS default viewer (Preview on macOS)
- `post_to_bluesky` — post image(s) to Bluesky with text and hashtags
- `add_template` — register a new ComfyUI workflow as a template
- `update_template` — update an existing template's workflow or metadata
- `delete_template` — remove a template

## Templates

Workflow templates are stored in `templates/`. Each template is a `.json` + `.meta.json` pair.
Add new templates by exporting a workflow from ComfyUI's browser UI and calling `add_template`.

## Output

Generated images are saved to `output/{YYYY-MM-DD}/{filename}`.
