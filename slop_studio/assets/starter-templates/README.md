# Workflow Templates

Each template is a pair of files in this directory: `<name>.json` (the ComfyUI workflow in API format) and `<name>.meta.json` (the metadata slop-studio uses for input injection and routing). Add a template by exporting a workflow from ComfyUI's *Save (API Format)* and calling `add_template` from Claude.

## Required metadata fields

Every `.meta.json` must declare:

- `name` — string matching the filename stem.
- `model` — string; the model slug (e.g. `flux-2-klein-9b-gguf`).
- `description` — string; human-readable summary for `list_templates`.

## Optional structural fields

- `inputs` — dict of `{input_name: {node_id, field, type, description}}`. Used by `queue_prompt` to inject values into the workflow. Entries with `input_type: "image"` are uploaded and wired to the named node.
- `aspect_ratios` — dict of `{label: dims}`. Used by the `aspect_ratio` parameter of `queue_prompt`. `dims` is a JSON object whose keys are consumed by `resolution_nodes` (below). Width-and-height templates use `{"width": 1424, "height": 1424}`; string-field templates (e.g. API nodes that take an `aspect_ratio: "3:4"` input directly) use `{"aspect_ratio": "3:4"}`.
- `resolution_nodes` — list of `{node_id, ...}`. Tells slop-studio which nodes to patch when an `aspect_ratio` is applied. Two modes per entry:
  - **Width/height mode:** `{"node_id", "width_field", "height_field"}` — writes `dims["width"]` / `dims["height"]` into the named fields.
  - **`field_map` mode:** `{"node_id", "field_map": {src_key: dest_field, ...}}` — writes `dims[src_key]` into `node.inputs[dest_field]` for each entry. Example for Gemini's `GeminiImage2Node`: `{"node_id": "35", "field_map": {"aspect_ratio": "aspect_ratio"}}` paired with `aspect_ratios: {"3:4": {"aspect_ratio": "3:4"}, ...}`.
- `expected_duration` — human-readable hint (e.g. `"30 seconds"`).

## Backend routing and cloud metadata

Three optional fields control cloud/local routing and multi-modal forward compatibility:

- **`backend`** — one of `"local"`, `"cloud"`, or `"either"`. Declares the template's intended backend. When absent, the router defers to `SLOP_STUDIO_DEFAULT_BACKEND`. Example: the shipped `flux2_klein` variants declare `"backend": "local"` because their GGUF model is local-only; `image_flux2` is cloud-compatible.
- **`output_keys`** — non-empty list of strings naming the output node-keys (e.g. `["images"]`, `["audio"]`, `["images", "videos"]`). Reserved for future multi-modal support — currently validated at write-time but not consumed at read-time.
- **`cloud_estimate_credits`** — non-negative `int` or `float`. Advisory cost estimate for a cloud run of this template; not billed or enforced. Purely documentation.

Example cloud template meta:

```json
{
  "name": "image_flux2",
  "model": "flux-2-dev-fp8mixed",
  "description": "Flux 2 Dev single-reference image edit.",
  "backend": "cloud",
  "output_keys": ["images"],
  "cloud_estimate_credits": 20,
  "expected_duration": "45 seconds",
  "inputs": {
    "prompt": { "node_id": "68:6", "field": "text", "type": "required", "description": "Edit instruction." },
    "image":  { "node_id": "46",   "field": "image", "type": "required", "input_type": "image", "description": "Reference image." }
  }
}
```

> The shipped `templates/image_flux2.meta.json` does not currently declare the three Story 6.6 cloud fields — the example above is illustrative. The `flux2_klein` variants do declare `"backend": "local"`.

See [../docs/comfy-cloud-integration.md](../docs/comfy-cloud-integration.md) for the routing rationale.
