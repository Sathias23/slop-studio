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

## Enum inputs

An input declaring `"input_type": "enum"` lets one user-facing choice drive several
workflow fields at once. It is how the Krea-2 templates expose their style LoRAs: the
editor's CustomCombo node auto-fills a LoRA's trigger word in the UI, but that plumbing
does not run over the API, so the sidecar carries the mapping itself.

```json
"style": {
  "node_id": "30:15", "field": "lora_name",
  "type": "optional", "input_type": "enum",
  "default": "none", "prompt_input": "prompt",
  "options": {
    "none": {},
    "darkbrush": {
      "value": "krea2_darkbrush.safetensors",
      "prompt_suffix": "monochrome ink wash style",
      "patches": [{"node_id": "30:3", "field": "model", "value": ["30:15", 0]}]
    }
  }
}
```

Per option:

- `value` — written to the enum input's own `node_id`/`field`. An option without one
  (`"none"` above) leaves that field at its shipped default.
- `patches` — extra `{node_id, field, value}` writes. A `value` of `[node_id, slot]` is a
  link reference, so a patch can rewire the graph — `darkbrush` above moves the sampler's
  `model` input from the bare checkpoint onto the LoRA loader. Leaving the LoRA loader
  unreachable by default matters: ComfyUI validates combo widgets on every node reachable
  from an output, so a permanently-wired loader would demand the LoRA file even when
  unused.
- `prompt_prefix` / `prompt_suffix` — text joined (comma-separated) onto the value of the
  input named by the enum's `prompt_input`, before injection.

`default` names the option applied when the caller omits the input; an option the template
doesn't declare is a terminal `invalid_inputs` error naming the supported set. Because
`get_template` returns the sidecar verbatim, the options and their descriptions are
self-documenting to the caller.

## Seeds

`queue_prompt` randomizes every `seed` / `noise_seed` field before submitting, so repeated
calls don't return ComfyUI's cached result. A template that declares a `seed` **input** is
the exception: a value the caller passes explicitly is left alone, which is what makes a
generation reproducible. Every other seed in the workflow is still randomized.

## Backend routing and cloud metadata

Three optional fields control cloud/local routing and multi-modal forward compatibility:

- **`backend`** — one of `"local"`, `"cloud"`, or `"either"`. Declares the template's intended backend. When absent, the router defers to `SLOP_STUDIO_DEFAULT_BACKEND`. Example: the shipped `flux2_klein` variants declare `"backend": "local"` because their GGUF model is local-only; `image_flux2` is cloud-compatible.
- **`output_keys`** — non-empty list of strings naming the ComfyUI output collection keys the template's terminal node writes (e.g. `["images"]`, `["3d"]`, `["audio"]`). Retrieval is key-agnostic: `get_image` scans a fixed order — `images`, `3d`, `gifs`, `videos`, `audio` — and returns the first file it finds, preferring images. So this field documents the template's output kind (and is asserted by the starter-template canaries in `tests/test_templates.py`) rather than being looked up at read-time. A template whose real output is a mesh must therefore not also carry an image-saving node, or the image would win — the shipped `image_to_3d_*` templates end in a single `SaveGLB` for that reason. Note `SaveGLB` reports `{filename, subfolder}` entries under the `3d` key, while `Save3DAdvanced` reports a bare path list that retrieval cannot consume.
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
