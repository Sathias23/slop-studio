---
stepsCompleted: [1, 2, 3, 4, 5, 6]
inputDocuments: []
workflowType: 'research'
lastStep: 1
research_type: 'technical'
research_topic: 'Flux.2 Klein image inputs in ComfyUI'
research_goals: 'Understand nodes/models required for image inputs, how to pass images via ComfyUI API, and relevant integration patterns'
user_name: 'Brad'
date: '2026-04-03'
web_research_enabled: true
source_verification: true
---

# Adding Image Inputs to Flux.2 Klein Workflows: Comprehensive Technical Research

**Date:** 2026-04-03
**Author:** Brad
**Research Type:** Technical Implementation Research

---

## Research Overview

This research investigates how to add image input support to Flux.2 Klein workflows in ComfyUI, specifically in the context of slop-studio's MCP-based image generation pipeline. The research covers four key areas: (1) the nodes and models required for Klein's native image editing, (2) how to pass images into ComfyUI via its HTTP API, (3) architectural design decisions for extending slop-studio's template system, and (4) a concrete implementation roadmap with code examples.

The central finding is that Flux.2 Klein has **built-in image editing** using a `ReferenceLatent` conditioning approach — the same checkpoint handles both text-to-image and image editing, requiring no additional model downloads. Integration with slop-studio requires approximately 30 lines of new code (an `_upload_image()` function), a minor async change to `_inject_inputs()`, a new workflow template, and the addition of Pillow as a dependency.

For the full executive summary and strategic recommendations, see the **Research Synthesis** section at the end of this document.

---

<!-- Content will be appended sequentially through research workflow steps -->

## Technical Research Scope Confirmation

**Research Topic:** Flux.2 Klein image inputs in ComfyUI
**Research Goals:** Understand nodes/models required for image inputs, how to pass images via ComfyUI API, and relevant integration patterns

**Technical Research Scope:**

- Architecture Analysis - image conditioning approaches (img2img, IP-Adapter, ControlNet, Redux), node wiring
- Implementation Approaches - required custom nodes, model downloads, workflow patterns
- Technology Stack - specific models (CLIP vision, IP-Adapter, ControlNet), node packs, Flux.2 Klein compatibility
- Integration Patterns - ComfyUI API image upload, workflow JSON structure, base64 vs file upload
- Performance Considerations - VRAM requirements, model loading, Klein-specific constraints

**Research Methodology:**

- Current web data with rigorous source verification
- Multi-source validation for critical technical claims
- Confidence level framework for uncertain information
- Comprehensive technical coverage with ComfyUI-specific insights

**Scope Confirmed:** 2026-04-03

## Technology Stack Analysis

### Approach 1: Native Flux.2 Klein Image Editing (Recommended)

Flux.2 Klein has **built-in image editing** — it's not just a text-to-image model. The same checkpoint handles both generation and editing natively, using a reference-latent conditioning approach rather than the older img2img denoise trick.

**Core Node Pipeline (from official BFL workflow):**

1. **LoadImage** — loads the input image(s)
2. **VAEEncode** — encodes input image to latent space
3. **ReferenceLatent** — fuses the encoded reference image(s) into the conditioning. Multiple `ReferenceLatent` nodes can be chained for multi-image reference editing
4. **CLIPTextEncode** — encodes the edit instruction/prompt
5. **CFGGuider** (cfg_scale: 5) — balances prompt adherence vs. reference preservation
6. **RandomNoise** — provides the seed for reproducibility
7. **Flux2Scheduler** (steps: 20 for base, 4 for distilled) — prepares the denoising schedule
8. **KSamplerSelect** (sampler: "euler") — selects the sampling algorithm
9. **SamplerCustomAdvanced** — performs the actual denoising
10. **VAEDecode** — decodes latent back to pixel space
11. **SaveImage** — outputs the result

_Key Insight: The image flows through VAEEncode → ReferenceLatent → CFGGuider conditioning, NOT through a traditional img2img denoise pipeline. This is a native architecture feature of Flux.2 Klein._
_Source: [ComfyUI Flux.2 Klein 4B Guide](https://docs.comfy.org/tutorials/flux/flux-2-klein)_
_Source: [Comfy-Org workflow_templates](https://github.com/Comfy-Org/workflow_templates)_

### Approach 2: Traditional Img2Img (Simpler, Less Capable)

A simpler alternative uses the standard img2img pattern: load an image, encode it to latent space, and sample on it with denoise < 1.0.

**Node Pipeline:**

1. **LoadImage** — loads the input image
2. **VAEEncode** — encodes to latent
3. **KSampler** — samples with denoise 0.3–0.8 (controls how much the image changes)
4. **VAEDecode** → **SaveImage**

_This approach is less precise — it globally transforms the image based on the prompt rather than making targeted edits._
_Source: [RunComfy Flux Img2Img Workflow](https://www.runcomfy.com/comfyui-workflows/comfyui-flux-img2img-workflow)_

### Approach 3: IP-Adapter / ControlNet (Style Transfer & Structure)

For style transfer or structural guidance from a reference image:

**IP-Adapter Nodes:**
- **Flux Load IPAdapter** — loads the IP-Adapter model
- **Apply Flux IPAdapter** (strength ≤ 1.0) — applies image conditioning
- Requires: `ip-adapter_flux.safetensors` in `models/xlabs/ipadapters/`
- Requires: CLIP Vision model from DualCLIPLoader

**ControlNet Nodes:**
- **ControlNet Union** — supports canny, depth, tile, blur, pose, gray, low-quality
- Models stored in `models/xlabs/controlnets/`

_Note: IP-Adapter and ControlNet models were trained for Flux.1 Dev. Compatibility with Flux.2 Klein is not officially confirmed and may produce unpredictable results._
_Source: [Stable Diffusion Tutorials - IP-Adapter ControlNet LoRA for Flux](https://www.stablediffusiontutorials.com/2024/08/ip-adapter-controlnet-lora-for-flux.html)_
_Source: [XLabs-AI flux-ip-adapter](https://huggingface.co/XLabs-AI/flux-ip-adapter)_

### Approach 4: Flux Redux (Image Variations)

For generating variations of an existing image:

**Nodes:**
- **StyleModelLoader** — loads the Redux adapter
- **CLIPVisionLoader** — loads the CLIP vision model
- **CLIPVisionEncode** — encodes the reference image
- **Apply Style Model** — applies conditioning (chainable for multi-image blending)

**Required Models:**
- `flux1-redux-dev.safetensors` → `models/style_models/`
- SigLIP CLIP Vision model → `models/clip_vision/`

_Redux is a lightweight adapter for image variation — no prompt required. Can be combined with a text prompt for guided variations using AdvancedReduxControl._
_Source: [ComfyUI Wiki - Flux Redux Tutorial](https://comfyui-wiki.com/en/tutorial/advanced/flux-redux-workflow-step-by-step-guide)_
_Source: [Stable Diffusion Art - Flux Redux](https://stable-diffusion-art.com/flux-redux/)_

### Required Models (Native Klein Image Edit)

| Model | File | Location | Purpose |
|-------|------|----------|---------|
| **Flux.2 Klein 4B (FP8)** | `flux-2-klein-base-4b-fp8.safetensors` | `models/diffusion_models/` | Main diffusion model (4B params) |
| **Flux.2 Klein 9B (GGUF)** | `Flux-2-Klein-9B-KV-Q8_0.gguf` | `models/unet/` | Main diffusion model (9B params, quantized) |
| **Flux.2 Klein 9B (FP8)** | `flux-2-klein-9b-fp8.safetensors` | `models/diffusion_models/` | Main diffusion model (9B, full precision quantized) |
| **Text Encoder (4B)** | `qwen_3_4b.safetensors` | `models/text_encoders/` | Qwen 3 4B for 4B Klein |
| **Text Encoder (9B)** | `qwen_3_8b_fp8mixed.safetensors` | `models/text_encoders/` | Qwen 3 8B for 9B Klein |
| **VAE** | `flux2-vae.safetensors` | `models/vae/` | Shared VAE for all Flux.2 |

_For GGUF models, the **ComfyUI-GGUF** custom node by city-96 must be installed._
_Source: [docs.comfy.org Flux.2 Klein 4B Guide](https://docs.comfy.org/tutorials/flux/flux-2-klein)_
_Source: [Kombitz - Flux.2 Klein 9B KV GGUF](https://www.kombitz.com/2026/03/20/how-to-use-flux-2-klein-9b-kv-image-edit-gguf-in-comfyui/)_
_Source: [Unsloth FLUX.2-klein-9B-GGUF](https://huggingface.co/unsloth/FLUX.2-klein-9B-GGUF)_

### VRAM Requirements

| Variant | VRAM | Speed (RTX 5090) |
|---------|------|-------------------|
| 4B Distilled (4 steps) | ~8.4 GB | ~1.2s |
| 4B Base (20 steps) | ~9.2 GB | ~17s |
| 9B Base | ~20-24 GB | Slower |
| 9B GGUF Q5/Q4 | ~12-16 GB | Varies |

_The 4B distilled variant is ideal for low-VRAM consumer hardware (12GB or less)._
_Source: [ComfyUI Blog - FLUX.2 Klein 4B & 9B](https://blog.comfy.org/p/flux2-klein-4b-fast-local-image-editing)_
_Source: [docs.comfy.org](https://docs.comfy.org/tutorials/flux/flux-2-klein)_

### ComfyUI API: Passing Images

**Step 1: Upload the image**

```python
import requests

def upload_image(filepath, server="127.0.0.1:8188", overwrite=True):
    with open(filepath, "rb") as f:
        resp = requests.post(
            f"http://{server}/upload/image",
            files={"image": f},
            data={"overwrite": "true"} if overwrite else {}
        )
    data = resp.json()
    # Returns the filename (e.g. "my_image.png")
    path = data["name"]
    if data.get("subfolder"):
        path = data["subfolder"] + "/" + path
    return path
```

**Step 2: Reference the uploaded image in the workflow JSON**

Set the LoadImage node's `image` input to the returned filename:

```python
# Upload
uploaded_name = upload_image("photo.png")

# Set in workflow prompt JSON
workflow["76"]["inputs"]["image"] = uploaded_name  # node 76 = LoadImage
```

**Step 3: Queue the workflow**

```python
import json, urllib.request

def queue_prompt(workflow, server="127.0.0.1:8188"):
    data = json.dumps({"prompt": workflow}).encode("utf-8")
    req = urllib.request.Request(f"http://{server}/prompt", data=data)
    return json.loads(urllib.request.urlopen(req).read())
```

**Key API Endpoints:**
- `POST /upload/image` — upload image (multipart/form-data)
- `POST /prompt` — queue a workflow
- `GET /history/{prompt_id}` — poll for results
- `GET /view?filename=...` — retrieve output images
- `WS /ws` — WebSocket for real-time status

_Source: [9elements - Hosting a ComfyUI Workflow via API](https://9elements.com/blog/hosting-a-comfyui-workflow-via-api/)_
_Source: [GitHub - sbszcz/image-upload-comfyui-example](https://github.com/sbszcz/image-upload-comfyui-example)_
_Source: [ViewComfy - Production-Ready ComfyUI API Guide](https://www.viewcomfy.com/blog/building-a-production-ready-comfyui-api)_

### Relevance to slop-studio

Your current `flux2_klein` template uses the 9B GGUF model for text-to-image only. To add image input support, you would need:

1. **A new workflow template** (or variant) that includes a `LoadImage` node wired through the native Klein edit pipeline (`ReferenceLatent` → `CFGGuider` → `SamplerCustomAdvanced`)
2. **A new input type in `.meta.json`** — an `image` input pointing to the `LoadImage` node
3. **Image upload handling in the MCP tool** — the `queue_prompt` tool (or a new tool) needs to upload the image via `/upload/image` before setting the filename in the workflow JSON
4. **The `flux2-vae.safetensors`** must be available (likely already is if text-to-image works)

_No additional model downloads are needed for the native edit approach — the same Klein checkpoint handles both generation and editing._

## Integration Patterns Analysis

### ComfyUI API Architecture

ComfyUI exposes a local HTTP + WebSocket API. The key endpoints for image input workflows:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/upload/image` | POST | Upload image files (multipart/form-data) |
| `/prompt` | POST | Queue a workflow for execution |
| `/ws?clientId={id}` | WS | Real-time execution status & progress |
| `/history/{prompt_id}` | GET | Poll for completion and retrieve result metadata |
| `/view?filename={name}` | GET | Retrieve output images |

_Source: [9elements - Hosting a ComfyUI Workflow via API](https://9elements.com/blog/hosting-a-comfyui-workflow-via-api/)_
_Source: [ComfyUI API Endpoints Guide](https://learncodecamp.net/comfyui-api-endpoints-complete-guide/)_

### Image Upload Protocol

**Endpoint:** `POST /upload/image`

**Request:** multipart/form-data with fields:
- `image` (file) — the image data with filename and MIME type
- `type` (string) — `"input"` (default), `"output"`, or `"temp"`
- `subfolder` (string, optional) — subdirectory within the type folder
- `overwrite` (string, optional) — `"true"` to replace existing

**Response:** JSON `{"name": "filename.png", "subfolder": "", "type": "input"}`

The uploaded file lands in `ComfyUI/input/` (or `ComfyUI/input/{subfolder}/`) and the returned `name` is what you set on the LoadImage node.

**Python implementation pattern:**
```python
import requests

def upload_image(filepath, server="127.0.0.1:8188", overwrite=True):
    with open(filepath, "rb") as f:
        resp = requests.post(
            f"http://{server}/upload/image",
            files={"image": (os.path.basename(filepath), f, "image/png")},
            data={"type": "input", "overwrite": "true"} if overwrite else {"type": "input"}
        )
    resp.raise_for_status()
    return resp.json()["name"]
```

_Source: [GitHub - sbszcz/image-upload-comfyui-example](https://github.com/sbszcz/image-upload-comfyui-example)_
_Source: [Medium - ComfyUI API Part 3: Img2Img Workflow](https://medium.com/@yushantripleseven/comfyui-using-the-api-part-3-5042da5fc75c)_

### LoadImage Node API Format

In the workflow JSON (API format), a LoadImage node looks like:

```json
{
  "76": {
    "inputs": {
      "image": "uploaded_filename.png",
      "upload": "image"
    },
    "class_type": "LoadImage"
  }
}
```

The `image` field takes the **filename** returned by `/upload/image`, NOT a full path. ComfyUI resolves it from its `input/` directory.

**Node outputs:** `[76, 0]` = pixel IMAGE tensor, `[76, 1]` = alpha MASK tensor.

_Source: [ComfyUI Community Manual - LoadImage](https://blenderneko.github.io/ComfyUI-docs/Core%20Nodes/Image/LoadImage/)_
_Source: [ComfyUI Wiki - Load Image](https://comfyui-wiki.com/en/comfyui-nodes/image/load-image)_

### Workflow JSON Modification Pattern

The ComfyUI API format uses numeric string keys for node IDs. To inject an image reference programmatically:

```python
# Load workflow template
workflow = json.load(open("template.json"))

# Upload image and get filename
uploaded_name = upload_image("photo.png")

# Inject into LoadImage node
workflow["76"]["inputs"]["image"] = uploaded_name

# Queue
requests.post(f"http://{server}/prompt", json={"prompt": workflow})
```

Node outputs are referenced as `[node_id, output_index]` arrays. For example, the LoadImage node (ID 76) feeding into a VAEEncode node would appear as:

```json
{
  "77": {
    "inputs": {
      "pixels": ["76", 0]
    },
    "class_type": "VAEEncode"
  }
}
```

_Source: [docs.comfy.org - Workflow JSON](https://docs.comfy.org/specs/workflow_json)_
_Source: [Medium - ComfyUI API Part 1](https://medium.com/@yushantripleseven/comfyui-using-the-api-261293aa055a)_

### Integration with slop-studio's Existing Architecture

**Current architecture** (from codebase analysis):
- `comfyui.py`: `_inject_inputs()` sets `workflow[node_id]["inputs"][field] = value` for text/number inputs
- `server.py`: `queue_prompt()` MCP tool takes `template_name`, `inputs` dict, and `aspect_ratio`
- `.meta.json`: Defines input mappings with `node_id`, `field`, `type`, `description`

**The gap:** Currently `_inject_inputs()` only handles primitive values (strings, numbers). Image inputs require a **two-phase injection**:

1. **Upload phase** — POST the image file to `/upload/image`, get back a filename
2. **Inject phase** — Set `workflow[node_id]["inputs"]["image"] = uploaded_filename`

**Proposed meta.json input type for images:**

```json
{
  "inputs": {
    "prompt": {
      "node_id": "6",
      "field": "text",
      "type": "required",
      "description": "Edit instruction"
    },
    "image": {
      "node_id": "76",
      "field": "image",
      "type": "required",
      "input_type": "image",
      "description": "Source image to edit"
    }
  }
}
```

A new `input_type: "image"` flag would tell `_inject_inputs()` to:
1. Treat the value as a **file path** (local) or **URL** (remote)
2. Upload via `/upload/image` before injection
3. Set the node's `image` field to the returned filename

**Image source options for the MCP tool:**
- **File path** — image already on disk (e.g., from a previous generation in `output/`)
- **URL** — fetch externally, save to temp, then upload
- **Previous output** — reference an image from a prior `get_image` call

_Confidence: HIGH — this pattern is well-established in ComfyUI API integrations and aligns cleanly with slop-studio's existing input injection architecture._

### Alternative: comfyui-tooling-nodes (No Upload Needed)

The [comfyui-tooling-nodes](https://github.com/Acly/comfyui-tooling-nodes) custom node pack provides `LoadImageBase64` — a node that accepts base64-encoded image data directly in the workflow JSON, bypassing the upload endpoint entirely.

**Pros:** Simpler integration (one API call instead of two), no temp files
**Cons:** Larger JSON payloads, requires installing a custom node pack, may hit request size limits for large images

_Source: [GitHub - Acly/comfyui-tooling-nodes](https://github.com/Acly/comfyui-tooling-nodes)_

### Data Flow: Complete Image Edit Integration

```
MCP Client (Claude)
  ↓ queue_prompt(template="flux2_klein_edit", inputs={"prompt": "change bg to forest", "image": "/path/to/photo.png"})
  
slop-studio server.py
  ↓ Detects input_type: "image" for the "image" input
  
comfyui.py — upload phase
  ↓ POST /upload/image with photo.png → returns "photo.png"
  
comfyui.py — inject phase
  ↓ workflow["76"]["inputs"]["image"] = "photo.png"
  ↓ workflow["6"]["inputs"]["text"] = "change bg to forest"
  ↓ _randomize_seeds(), _inject_resolution()
  
comfyui.py — submit
  ↓ POST /prompt with modified workflow
  ↓ Returns prompt_id
  
MCP Client polls via check_job → get_image
```

### Security Considerations

- **File validation:** Verify uploaded files are actual images (check magic bytes, not just extension)
- **Path traversal:** Sanitize file paths to prevent directory traversal attacks
- **Size limits:** ComfyUI doesn't enforce upload size limits by default — implement client-side limits
- **Temp file cleanup:** If downloading URLs, clean up temp files after upload
- **Overwrite flag:** Use `overwrite=true` with unique filenames (e.g., UUID-prefixed) to avoid collisions between concurrent users

_Source: [ViewComfy - Building a Production-Ready ComfyUI API](https://www.viewcomfy.com/blog/building-a-production-ready-comfyui-api)_

## Architectural Patterns and Design

### Design Decision 1: Template Strategy — Separate vs. Unified Workflows

**Option A: Separate edit template (Recommended)**

Create a new `flux2_klein_edit` template alongside the existing `flux2_klein` text-to-image template. Each is a distinct `.json` + `.meta.json` pair.

```
templates/
  flux2_klein.json           # text-to-image (existing)
  flux2_klein.meta.json
  flux2_klein_edit.json      # image edit (new)
  flux2_klein_edit.meta.json
```

**Pros:**
- Clean separation of concerns — each template does one thing
- Different node pipelines (ReferenceLatent vs. EmptyLatent) make combining awkward
- Different default settings (e.g., cfg, steps, denoise) for each mode
- Easier for the LLM to choose the right tool ("generate" vs. "edit")

**Cons:**
- Two templates to maintain when updating the Klein model

**Option B: Unified template with optional image input**

A single workflow with the image input nodes bypassed by default (ComfyUI supports disabling nodes). When an image is provided, the bypass is lifted.

**Pros:** One template covers both modes
**Cons:** Complex JSON manipulation (toggling node bypass states), harder to reason about, fragile

**Recommendation:** Option A. Klein's official workflows are already separate (text-to-image vs. image edit), and slop-studio's template system is designed for one-template-per-purpose.

_Source: [docs.comfy.org - Flux.2 Klein 4B Guide](https://docs.comfy.org/tutorials/flux/flux-2-klein) (provides separate downloadable workflows for each mode)_

### Design Decision 2: Image Input Architecture in `.meta.json`

The current `.meta.json` schema supports only primitive value injection. Image inputs need a new `input_type` discriminator:

```json
{
  "inputs": {
    "prompt": {
      "node_id": "6",
      "field": "text",
      "type": "required",
      "description": "Edit instruction describing what to change"
    },
    "image": {
      "node_id": "76",
      "field": "image",
      "type": "required",
      "input_type": "image",
      "description": "Source image to edit (file path or output from a previous generation)"
    },
    "reference_image": {
      "node_id": "81",
      "field": "image",
      "type": "optional",
      "input_type": "image",
      "description": "Second reference image for style/material transfer (multi-reference edit)"
    }
  }
}
```

**Key design choices:**

1. **`input_type: "image"` as the discriminator** — backwards-compatible; existing templates without `input_type` default to text/number injection as before
2. **Accept file paths, not raw data** — the MCP client (Claude) provides a local file path; slop-studio handles the upload. This keeps the MCP tool interface clean
3. **Support previous outputs** — accept paths like `output/2026-04-03/ComfyUI_00042_.png` so the LLM can chain generate → edit workflows
4. **Optional second image** — Klein supports multi-reference editing; the second image node can be optional

_Confidence: HIGH — this extends the existing schema without breaking changes._

### Design Decision 3: Upload Lifecycle in `comfyui.py`

The upload needs to happen **after** template loading but **before** workflow submission. The cleanest insertion point is inside `_inject_inputs()`:

```
Current flow:
  queue_prompt() → _inject_inputs() → _randomize_seeds() → _inject_resolution() → POST /prompt

Proposed flow:
  queue_prompt() → _upload_images() → _inject_inputs() → _randomize_seeds() → _inject_resolution() → POST /prompt
```

**`_upload_images()` responsibilities:**
1. Scan meta.json inputs for `input_type: "image"`
2. For each image input with a provided value:
   - Validate the file exists and is a valid image
   - Upload via `POST /upload/image` with `overwrite=true` and a UUID-prefixed filename
   - Replace the value in the inputs dict with the returned ComfyUI filename
3. Skip optional image inputs that aren't provided
4. Then `_inject_inputs()` proceeds normally — it just sees a string filename to inject

**Error handling:**
- File not found → return `{"status": "error", "error_type": "validation", "error": "Image file not found: ..."}`
- Upload fails → return `{"status": "error", "error_type": "comfyui_unreachable", ...}`
- Invalid image → return `{"status": "error", "error_type": "validation", "error": "File is not a valid image: ..."}`

_This pattern keeps the upload logic isolated and testable, without touching the existing injection/seed/resolution code._

### Design Decision 4: Klein Edit Workflow Node Architecture

Based on the official BFL workflow analysis, the image edit pipeline follows this architecture:

```
┌─────────────┐     ┌───────────┐     ┌──────────────────┐     ┌────────────┐
│  LoadImage   │────▶│ VAEEncode │────▶│ ReferenceLatent  │────▶│ CFGGuider  │
│  (node 76)   │     │           │     │ (fuses reference │     │ (cfg: 5)   │
└─────────────┘     └───────────┘     │  into conditioning)│   └─────┬──────┘
                                       └──────────────────┘         │
┌─────────────┐     ┌───────────┐                                    │
│ CLIPTextEnc  │────▶│ positive  │───────────────────────────────────▶│
│  (prompt)    │     │ condition │                                    │
└─────────────┘     └───────────┘                                    │
                                                                      ▼
┌─────────────┐     ┌──────────────┐     ┌──────────────────────────────┐
│ RandomNoise  │────▶│ KSampler     │────▶│ SamplerCustomAdvanced        │
│ (seed)       │     │ Select(euler)│     │                              │
└─────────────┘     └──────────────┘     └──────────┬───────────────────┘
                                                      │
┌─────────────────┐     ┌───────────┐                 │
│ Flux2Scheduler   │────▶│ (steps,   │─────────────────┘
│ (steps: 20)      │     │  denoise) │
└─────────────────┘     └───────────┘
                                                      │
                                                      ▼
                                           ┌───────────┐     ┌───────────┐
                                           │ VAEDecode  │────▶│ SaveImage │
                                           └───────────┘     └───────────┘
```

**For multi-reference editing**, a second `LoadImage` (node 81) feeds through its own `VAEEncode` → `ReferenceLatent`, which chains into the first `ReferenceLatent` before reaching the `CFGGuider`.

**Key architectural insight:** The `ReferenceLatent` node is the critical differentiator from standard img2img. It doesn't add noise to the reference — it conditions the generation to respect the reference's content, making it much more precise for targeted edits.

_Source: [ComfyUI Blog - FLUX.2 Klein 4B & 9B](https://blog.comfy.org/p/flux2-klein-4b-fast-local-image-editing)_
_Source: [Comfy-Org workflow_templates](https://github.com/Comfy-Org/workflow_templates)_

### Design Decision 5: Subgraph vs. Flat Workflow for Templates

ComfyUI now supports **subgraphs** — collapsing node groups into reusable single nodes. The official Klein edit workflows use subgraphs (the "Image Edit (Flux.2 Klein 4B)" block is a subgraph).

**For slop-studio templates:**

- **Use the flat (expanded) API format** — subgraphs are a UI convenience. When exported via "Export (API Format)", they flatten to individual nodes with numeric IDs. The API format is what `/prompt` consumes.
- **Don't try to manipulate subgraph internals via the API** — the API format doesn't preserve subgraph boundaries
- **Export from ComfyUI browser UI** — load the official workflow, make any adjustments, then File → Export (API Format) to get the flat JSON

_Source: [docs.comfy.org - Subgraph](https://docs.comfy.org/interface/features/subgraph)_

### Design Decision 6: MCP Tool Interface Design

**Option A: Extend `queue_prompt` (Recommended)**

The existing `queue_prompt` tool already accepts an `inputs` dict. Image paths can be passed as values:

```python
queue_prompt(
    template_name="flux2_klein_edit",
    inputs={
        "prompt": "Change the background to a forest",
        "image": "/path/to/output/2026-04-03/ComfyUI_00042_.png"
    },
    aspect_ratio="1:1"
)
```

The tool internally detects `input_type: "image"` from the meta.json and handles the upload transparently. No new MCP tools needed.

**Option B: Separate `upload_image` tool**

Expose image upload as a separate MCP tool, requiring the LLM to call upload first, then queue_prompt with the returned filename.

**Recommendation:** Option A. Keep it simple — one tool call from the LLM's perspective. The upload is an implementation detail that shouldn't leak into the MCP interface.

### Scalability and Performance Considerations

| Factor | Impact | Mitigation |
|--------|--------|------------|
| Image upload latency | Adds ~100-500ms per image | Acceptable for a 17-30s generation pipeline |
| VRAM for image edit vs. text-to-image | Similar (~8-9 GB for 4B, ~20-24 GB for 9B) | No additional VRAM overhead |
| Concurrent uploads | ComfyUI input/ folder could get cluttered | UUID-prefixed filenames + periodic cleanup |
| Large images | High-res input → more VRAM at encode time | ComfyUI auto-resizes; no action needed |
| Multi-reference (2 images) | ~2x upload time, same generation time | Parallel upload if needed |

_Source: [docs.comfy.org](https://docs.comfy.org/tutorials/flux/flux-2-klein)_
_Source: [ViewComfy - Production-Ready ComfyUI API Guide](https://www.viewcomfy.com/blog/building-a-production-ready-comfyui-api)_

## Implementation Approaches and Technology Adoption

### Implementation Roadmap

**Phase 1: Workflow Template (Day 1)**

1. Load the official Klein image edit workflow in ComfyUI browser UI
2. Adjust for the 9B GGUF model (matching your existing `flux2_klein` setup)
3. Export via File → Export (API Format) to get the flat JSON
4. Save as `templates/flux2_klein_edit.json`
5. Create `templates/flux2_klein_edit.meta.json` with image input mapping

**Phase 2: Core Image Upload Support (Day 1-2)**

Add `_upload_image()` to `comfyui.py`:

```python
import os
import uuid
import httpx

async def _upload_image(file_path: str) -> str:
    """Upload a local image file to ComfyUI's input directory.
    
    Returns the ComfyUI filename for use in LoadImage nodes.
    Raises ValueError for invalid files, httpx errors for upload failures.
    """
    if not os.path.isfile(file_path):
        raise ValueError(f"Image file not found: {file_path}")
    
    # Validate it's actually an image (Pillow verify)
    from PIL import Image
    try:
        with Image.open(file_path) as img:
            img.verify()
    except Exception:
        raise ValueError(f"File is not a valid image: {file_path}")
    
    # UUID prefix to avoid filename collisions
    ext = os.path.splitext(file_path)[1] or ".png"
    upload_name = f"{uuid.uuid4().hex[:12]}{ext}"
    
    async with httpx.AsyncClient() as client:
        with open(file_path, "rb") as f:
            resp = await client.post(
                f"{COMFYUI_URL}/upload/image",
                files={"image": (upload_name, f, "image/png")},
                data={"type": "input", "overwrite": "true"},
            )
        resp.raise_for_status()
    
    return resp.json()["name"]
```

**Key design notes:**
- Uses `httpx.AsyncClient` consistent with existing codebase (which uses httpx for all ComfyUI API calls)
- Pillow `verify()` checks magic bytes, not just extension — prevents uploading non-image files
- UUID prefix prevents filename collisions without needing cleanup
- Returns just the filename string, which is what LoadImage nodes expect

_Source: [GeeksforGeeks - Check If A File is Valid Image with Python](https://www.geeksforgeeks.org/python/check-if-a-file-is-valid-image-with-python/)_

**Phase 3: Input Injection Enhancement (Day 2)**

Modify `_inject_inputs()` to handle image inputs:

```python
async def _inject_inputs(workflow: dict, meta_inputs: dict, user_inputs: dict) -> None:
    """Inject user-provided input values into workflow nodes in-place.
    
    For inputs with input_type: "image", uploads the file first and
    injects the returned ComfyUI filename.
    """
    for name, value in user_inputs.items():
        defn = meta_inputs.get(name)
        if defn is None:
            continue
        
        node_id = defn.get("node_id")
        field = defn.get("field")
        if not node_id or not field:
            continue
        
        # Handle image inputs: upload first, then inject filename
        if defn.get("input_type") == "image":
            value = await _upload_image(value)
        
        node = workflow.get(node_id)
        if node and "inputs" in node:
            node["inputs"][field] = value
```

**Breaking change consideration:** `_inject_inputs()` becomes `async`. Since `queue_prompt()` is already `async` and calls it with a simple function call, the change is:
- `_inject_inputs(workflow, meta_inputs, user_inputs)` → `await _inject_inputs(workflow, meta_inputs, user_inputs)`

This is a one-line change in `queue_prompt()`.

**Phase 4: Meta.json Schema for Edit Template (Day 2)**

```json
{
  "name": "flux2_klein_edit",
  "model": "flux-2-klein-9b-gguf",
  "description": "Flux 2 Klein 9B image editing pipeline. Edit existing images with text prompts using reference-latent conditioning.",
  "expected_duration": "35 seconds",
  "inputs": {
    "prompt": {
      "node_id": "6",
      "field": "text",
      "type": "required",
      "description": "Text instruction describing the edit (e.g., 'Change the background to a forest')"
    },
    "image": {
      "node_id": "76",
      "field": "image",
      "type": "required",
      "input_type": "image",
      "description": "Source image to edit. Accepts a local file path (e.g., output from a previous generation)."
    }
  },
  "aspect_ratios": {
    "1:1": {"width": 1024, "height": 1024},
    "4:3": {"width": 1024, "height": 768},
    "3:4": {"width": 768, "height": 1024},
    "16:9": {"width": 1024, "height": 576},
    "9:16": {"width": 576, "height": 1024}
  },
  "resolution_nodes": [
    {"node_id": "47", "width_field": "width", "height_field": "height"}
  ]
}
```

**Note:** Node IDs (`"6"`, `"76"`, `"47"`) are placeholders — the actual IDs come from the exported workflow JSON. The aspect ratios for editing should respect the source image dimensions (1024x1024 is Klein's native resolution).

### Testing Strategy

**Test patterns match existing codebase** (pytest + respx + anyio):

```python
@pytest.mark.anyio
@respx.mock
async def test_queue_prompt_with_image_input(sample_templates, tmp_path):
    """Image inputs are uploaded before injection."""
    # Create a valid test image
    from PIL import Image
    img_path = tmp_path / "test.png"
    Image.new("RGB", (100, 100), "red").save(img_path)
    
    # Mock the upload endpoint
    respx.post(f"{COMFYUI_URL}/upload/image").mock(
        return_value=httpx.Response(200, json={"name": "abc123.png", "subfolder": "", "type": "input"})
    )
    # Mock the prompt endpoint
    respx.post(f"{COMFYUI_URL}/prompt").mock(
        return_value=httpx.Response(200, json={"prompt_id": "test-123"})
    )
    
    result = await comfyui.queue_prompt(
        "test_edit_template",
        {"prompt": "make it blue", "image": str(img_path)}
    )
    
    assert result["status"] == "success"
    # Verify upload was called
    upload_call = respx.calls[0]
    assert "/upload/image" in str(upload_call.request.url)
    # Verify the injected filename in the prompt
    prompt_call = respx.calls[1]
    body = json.loads(prompt_call.request.content)
    assert body["prompt"]["76"]["inputs"]["image"] == "abc123.png"


@pytest.mark.anyio
async def test_upload_rejects_non_image(tmp_path):
    """Non-image files are rejected before upload."""
    fake = tmp_path / "not_an_image.png"
    fake.write_text("this is not an image")
    
    with pytest.raises(ValueError, match="not a valid image"):
        await comfyui._upload_image(str(fake))


@pytest.mark.anyio
async def test_upload_rejects_missing_file():
    """Missing files are rejected."""
    with pytest.raises(ValueError, match="not found"):
        await comfyui._upload_image("/nonexistent/path.png")
```

**Test coverage targets:**
- Upload success path with mocked `/upload/image`
- Upload failure (ComfyUI unreachable)
- Invalid image file rejection
- Missing file rejection
- Optional image input (not provided) → skipped gracefully
- Backwards compatibility: existing text-only templates work unchanged

### Development Workflow

1. **Export the workflow:** Load Klein edit workflow in ComfyUI → adjust for 9B GGUF → Export (API Format)
2. **Identify node IDs:** Find the LoadImage node ID and CLIPTextEncode node ID in the exported JSON
3. **Create template pair:** `flux2_klein_edit.json` + `flux2_klein_edit.meta.json`
4. **Implement `_upload_image()`** in `comfyui.py`
5. **Make `_inject_inputs()` async** and add the `input_type: "image"` branch
6. **Update `queue_prompt()`** to `await _inject_inputs()`
7. **Add Pillow to dependencies** (if not already present)
8. **Write tests** matching the existing respx mock patterns
9. **End-to-end test:** Call `queue_prompt("flux2_klein_edit", {"prompt": "...", "image": "path"})` against a running ComfyUI

### Dependencies

| Dependency | Purpose | Already in project? |
|------------|---------|---------------------|
| `httpx` | Async HTTP client for upload | Yes (existing) |
| `Pillow` | Image validation (verify magic bytes) | **Needs adding** |
| `respx` | HTTP mocking for tests | Yes (existing) |
| `pytest-anyio` | Async test runner | Yes (existing) |

**Only Pillow needs to be added** — `pip install Pillow` or add to `pyproject.toml`.

### Risk Assessment and Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Workflow node IDs differ after export | Medium | High — wrong injection targets | Verify node IDs match after every re-export; add a test that loads the actual template |
| Pillow import slows down cold start | Low | Low | Lazy import inside `_upload_image()` only when needed |
| Large images cause ComfyUI timeouts | Low | Medium | ComfyUI handles resize internally; no client action needed |
| `_inject_inputs` becoming async breaks something | Low | Medium | Only one callsite (`queue_prompt`); change is trivial |
| ComfyUI `/upload/image` endpoint changes | Very Low | High | Pin ComfyUI version; endpoint has been stable since 2023 |

### Success Metrics

- `queue_prompt("flux2_klein_edit", {"prompt": "...", "image": "path"})` produces an edited image
- All existing text-to-image tests pass unchanged (backwards compatibility)
- Image validation rejects non-image files before upload
- LLM (Claude) can chain: generate → get_image → edit workflow in a single conversation

_Source: [Comfy-Org workflow_templates](https://github.com/Comfy-Org/workflow_templates)_
_Source: [Apatero - Flux 2 Klein ComfyUI Workflow Guide](https://www.apatero.com/blog/flux-2-klein-comfyui-workflow-guide)_

---

## Research Synthesis

### Executive Summary

Flux.2 Klein's native image editing capability makes it straightforward to add image inputs to slop-studio. The same 9B GGUF checkpoint already used for text-to-image generation also handles image editing via a `ReferenceLatent` conditioning pipeline — no additional models are needed. The ComfyUI API provides a well-established `/upload/image` endpoint for passing image files, and the integration fits cleanly into slop-studio's existing template-based architecture.

The implementation requires minimal changes to the codebase: a new `_upload_image()` async function (~30 lines), an `input_type: "image"` discriminator in the `.meta.json` schema, and making `_inject_inputs()` async to support the upload-before-inject pattern. The only new dependency is Pillow for image validation. All existing text-to-image functionality remains unchanged.

**Key Technical Findings:**

- Flux.2 Klein has **built-in image editing** via `ReferenceLatent` conditioning — not a traditional img2img denoise approach. This is more precise for targeted edits.
- The same checkpoint handles both generation and editing — no extra models to download or manage
- ComfyUI's `/upload/image` endpoint is stable, well-documented, and returns a filename for injection into `LoadImage` nodes
- Klein supports **single-reference** (edit one image) and **multi-reference** (combine two images) editing natively
- The 9B GGUF model requires ~20-24 GB VRAM for editing (same as generation); the 4B variant needs only ~8-9 GB
- Four alternative approaches exist (traditional img2img, IP-Adapter, ControlNet, Redux) but native editing is recommended

**Strategic Recommendations:**

1. **Start with native Klein edit** — it's the simplest path with no extra models and the best quality
2. **Use a separate template** (`flux2_klein_edit`) rather than trying to unify generate + edit in one workflow
3. **Extend `queue_prompt`** rather than creating a new MCP tool — keep the LLM interface simple
4. **Add Pillow** for image validation — prevents uploading non-image files via magic byte checking
5. **Chain workflows** — the LLM can generate → get_image → edit in a conversation, using the output path from one as input to the next

### Table of Contents

1. [Technical Research Scope Confirmation](#technical-research-scope-confirmation)
2. [Technology Stack Analysis](#technology-stack-analysis)
   - Approach 1: Native Klein Image Editing (Recommended)
   - Approach 2: Traditional Img2Img
   - Approach 3: IP-Adapter / ControlNet
   - Approach 4: Flux Redux
   - Required Models
   - VRAM Requirements
   - ComfyUI API: Passing Images
3. [Integration Patterns Analysis](#integration-patterns-analysis)
   - ComfyUI API Architecture
   - Image Upload Protocol
   - LoadImage Node API Format
   - Integration with slop-studio
   - Data Flow Diagram
4. [Architectural Patterns and Design](#architectural-patterns-and-design)
   - Template Strategy (Separate vs. Unified)
   - Image Input Architecture in `.meta.json`
   - Upload Lifecycle in `comfyui.py`
   - Klein Edit Workflow Node Architecture
   - MCP Tool Interface Design
5. [Implementation Approaches](#implementation-approaches-and-technology-adoption)
   - 4-Phase Implementation Roadmap
   - Code Examples (`_upload_image`, `_inject_inputs`, meta.json)
   - Testing Strategy with Code Examples
   - Dependencies and Risk Assessment

### Answers to Original Research Questions

**Q1: What nodes and models are required for image inputs with Flux.2 Klein?**

**Nodes (native edit pipeline):**
- `LoadImage` — loads the input image
- `VAEEncode` — encodes to latent space
- `ReferenceLatent` — fuses reference into conditioning (chainable for multi-image)
- `CLIPTextEncode` — encodes the edit instruction
- `CFGGuider` (cfg: 5) — balances prompt vs. reference
- `RandomNoise` + `KSamplerSelect` (euler) + `Flux2Scheduler` (steps: 20 base / 4 distilled)
- `SamplerCustomAdvanced` — performs denoising
- `VAEDecode` → `SaveImage`

**Models (no extras needed):**
- Same `flux-2-klein-9b` GGUF checkpoint already in use
- Same `qwen_3_8b_fp8mixed.safetensors` text encoder
- Same `flux2-vae.safetensors` VAE
- For GGUF: `ComfyUI-GGUF` custom node (likely already installed)

**Q2: How do we pass images into ComfyUI via the API?**

1. `POST /upload/image` with multipart/form-data (`image` file, `type: "input"`, `overwrite: "true"`)
2. Response returns `{"name": "filename.png"}` 
3. Set `workflow["<LoadImage_node_id>"]["inputs"]["image"] = "filename.png"`
4. `POST /prompt` with the modified workflow as usual

**Q3: Anything else relevant?**

- **Multi-reference editing** — Klein natively supports two input images (one for content, one for style/material). The meta.json can declare the second image as `type: "optional"`
- **Workflow chaining** — The LLM can generate an image, retrieve its path via `get_image`, then pass that path to the edit template. This enables iterative creative workflows
- **No aspect ratio changes needed for editing** — The edit workflow should respect the source image dimensions. Klein's native resolution is 1024x1024 but handles other sizes
- **Subgraphs in official workflows** — The BFL-provided workflows use ComfyUI subgraphs, but the API format flattens these. Export via File → Export (API Format) to get the flat JSON needed for templates
- **Performance parity** — Image editing takes the same time and VRAM as text-to-image generation. No performance penalty for the edit mode

### Source Documentation

**Primary Sources (Official):**
- [ComfyUI Flux.2 Klein 4B Guide](https://docs.comfy.org/tutorials/flux/flux-2-klein)
- [Comfy-Org Workflow Templates](https://github.com/Comfy-Org/workflow_templates)
- [ComfyUI Blog - FLUX.2 Klein 4B & 9B](https://blog.comfy.org/p/flux2-klein-4b-fast-local-image-editing)
- [ComfyUI Examples - Flux 2](https://comfyanonymous.github.io/ComfyUI_examples/flux2/)
- [Black Forest Labs - FLUX.2 Klein 4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B)
- [Unsloth FLUX.2-klein-9B-GGUF](https://huggingface.co/unsloth/FLUX.2-klein-9B-GGUF)

**Integration & API Sources:**
- [9elements - Hosting a ComfyUI Workflow via API](https://9elements.com/blog/hosting-a-comfyui-workflow-via-api/)
- [GitHub - sbszcz/image-upload-comfyui-example](https://github.com/sbszcz/image-upload-comfyui-example)
- [ViewComfy - Production-Ready ComfyUI API Guide](https://www.viewcomfy.com/blog/building-a-production-ready-comfyui-api)
- [Medium - ComfyUI API Part 3: Img2Img](https://medium.com/@yushantripleseven/comfyui-using-the-api-part-3-5042da5fc75c)
- [docs.comfy.org - Workflow JSON Spec](https://docs.comfy.org/specs/workflow_json)

**Community & Tutorial Sources:**
- [RunComfy - Flux 2 Klein 9B KV Image Edit](https://www.runcomfy.com/comfyui-workflows/flux-2-klein-9b-kv-image-edit-in-comfyui-precision-prompt-editing)
- [Next Diffusion - FLUX 2.0 Klein in ComfyUI](https://www.nextdiffusion.ai/tutorials/flux-2-0-klein-in-comfyui-fast-image-generation-and-editing)
- [Kombitz - Flux.2 Klein 9B KV GGUF](https://www.kombitz.com/2026/03/20/how-to-use-flux-2-klein-9b-kv-image-edit-gguf-in-comfyui/)
- [Civitai - FLUX.2-klein I2I v2.0](https://civitai.com/articles/25307/basic-workflow-flux2-klein-i2i-v20-or-4-in-1-image-editing)
- [Stable Diffusion Tutorials - IP-Adapter ControlNet for Flux](https://www.stablediffusiontutorials.com/2024/08/ip-adapter-controlnet-lora-for-flux.html)
- [Stable Diffusion Art - Flux Redux](https://stable-diffusion-art.com/flux-redux/)
- [Acly/comfyui-tooling-nodes](https://github.com/Acly/comfyui-tooling-nodes)

### Research Confidence Levels

| Finding | Confidence | Basis |
|---------|-----------|-------|
| Klein native edit uses ReferenceLatent conditioning | HIGH | Official BFL workflow JSON, multiple tutorials |
| Same checkpoint handles both generation and editing | HIGH | Official docs, HuggingFace model card |
| /upload/image API is stable and well-documented | HIGH | Multiple implementations, stable since 2023 |
| No additional models needed for native edit | HIGH | Verified against official workflow template |
| IP-Adapter/ControlNet compatibility with Klein | LOW | Trained for Flux.1 Dev, not officially confirmed for Flux.2 Klein |
| VRAM requirements (~20-24 GB for 9B) | MEDIUM | Community reports vary; depends on quantization and resolution |
| slop-studio implementation approach | HIGH | Based on direct codebase analysis |

---

**Technical Research Completion Date:** 2026-04-03
**Research Period:** Comprehensive technical analysis with current web verification
**Source Verification:** All claims cited with current sources; confidence levels assigned
**Research Methodology:** Multi-source web research, official documentation analysis, codebase exploration, workflow JSON inspection

_This research document serves as the technical foundation for implementing image input support in slop-studio's Flux.2 Klein workflow pipeline._
