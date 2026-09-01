# Art Project

This project uses [slop-studio](https://github.com/Sathias23/slop-studio) for conversational image and 3D generation via ComfyUI.

## Quick Start

Use `/generate <description>` to create an image. Example: `/generate a sunset over mountains, cinematic lighting`

## Available Tools

- `list_templates` — browse available workflow templates
- `get_template` — inspect inputs and aspect ratios for a template
- `queue_prompt` — submit a generation job
- `check_next_job` — poll multiple jobs for completion (use `wait: 30`)
- `get_image` — retrieve the output path (image, or a `.glb` mesh from an image-to-3D template) plus a base64 thumbnail for images
- `open_gallery` — open output(s) for viewing (single opens in OS viewer, multiple opens HTML gallery)
- `check_requirements` — read-only: report which model files a template needs and which are missing
- `download_models` — fetch a template's missing model files into ComfyUI's models directory
- `open_comfy_cloud_portal` — open the Comfy Cloud billing/account portal in the default browser
- `post_to_bluesky` — post image(s) to Bluesky with text and hashtags. Pass hashtags via the `tags` param (names without `#`), NOT inline in the body `text` — the tool appends them with proper richtext facets; any `#tag` written into `text` stays as plain text with no facet
- `add_template` — register a new ComfyUI workflow as a template
- `update_template` — update an existing template's workflow or metadata
- `delete_template` — remove a template

## Templates

Workflow templates are stored in `templates/`. Each template is a `.json` + `.meta.json` pair.
Add new templates by exporting a workflow from ComfyUI's browser UI and calling `add_template`.

## Backends

Templates may declare a `backend` field in their `.meta.json`: `"local"`, `"cloud"`, or `"either"`.
`SLOP_STUDIO_DEFAULT_BACKEND` (`"local"` or `"cloud"`) controls the fallback when the field is absent or set to `"either"`.
When `queue_prompt` reports `no_credits`, `auth_failed`, or `account_issue`, call `open_comfy_cloud_portal` to let the user resolve it in the browser.

## Image-to-3D

`image_to_3d_trellis2` (TRELLIS.2) and `image_to_3d_pixal3d` (Pixal3D) take one reference image and produce a textured `.glb` mesh with PBR maps. They take no text prompt — geometry and texture come entirely from the image, so pass a clear, single-subject photo. Optional inputs: `shape_resolution` (1024–2048, default 1536), `texture_resolution` (default 4096), `face_count` (default 700000).

These templates need ~9–10 GB of model downloads. Call `check_requirements` first and, if anything is missing, tell the user the total size before calling `download_models`. Expect 4–8 minutes per run on a 24 GB GPU, so poll `check_next_job` with a generous `wait`. Retrieve with `get_image` (no thumbnail — a mesh isn't a raster image) and view with `open_gallery`.

## Output

Generated images and meshes are saved to `output/{YYYY-MM-DD}/{filename}`.
