# Future Ideas

## CLIP Algebra

Instead of describing what you want with text prompts (which are inherently linear and sequential), operate directly on CLIP embedding vectors. Encode two prompts, add/subtract/interpolate their embeddings, and generate from the resulting point in latent space.

This is analogous to the classic word2vec arithmetic ("king - man + woman = queen") but for image generation. You could take the embedding for "cenobites playing cricket" and add the embedding for "oil painting by Rembrandt" to get a genuine blend in latent space — something no text prompt could describe.

Subtraction works too: "this image minus horror plus serenity" as a vector operation, navigating to points in the space that language can't reach.

### Inspiration

Wes Roth's framing of TurboQuant (Google, ICLR 2026): instead of giving X linear dimensions to describe a point, just point directly at that point in latent space. Prompts become coordinates, not descriptions.

TurboQuant's extreme quantization (3-bit with no accuracy loss) could make this practical — arithmetic on compressed embeddings rather than full float32 vectors, enabling real-time blending in a generation pipeline.

## Embedding Library

A natural extension of CLIP algebra: save interesting embedding coordinates and build a navigable map of latent space.

- Bookmark notable points (e.g. "corporate horror empty office", "cenobites playing cricket")
- Recall and remix saved points — interpolate between two bookmarks, add/subtract to explore neighbourhoods
- Build up a personal atlas of the interesting regions you've discovered
- Share bookmarks as compact vectors rather than trying to describe them in words

This gives you **navigation** through latent space, complementing the **exploration** that the token scrambler provides.

## Token Scrambler (CLIP Prompt Synonymiser)

Uses CLIP to replace tokens in a prompt string with top-k semantically adjacent alternatives. The result is a prompt that's close in embedding space but linguistically scrambled — pushing the model off the beaten path into unexpected territory.

Previously tested with SDXL where it produced genuinely unhinged results — the kind of raw, accidental cosmic horror that early AI art (ruDALLE, early Midjourney) was known for. Modern models like Flux are too good at interpreting language coherently, so the scrambler is a way to force the happy accidents that polished models iron out.

### Combined workflow

1. **Explore** with the token scrambler — find weird new regions of latent space
2. **Bookmark** interesting results as saved embeddings
3. **Navigate** between bookmarks using CLIP algebra — blend, interpolate, subtract
4. **Refine** with targeted prompt edits once you're in the right neighbourhood

## Image Inputs (img2img)

Add support for image inputs in workflow templates — feed an existing generated image back into the pipeline for img2img transformations. This would allow style transfer, morphing between concepts (e.g. turning an eldritch horror into a fluffy duck while preserving composition and lighting), inpainting, and iterative refinement without starting from scratch each time.

Requires templates that accept image inputs alongside text prompts, and a way to reference previous outputs by path or job ID.

## Gemma 4 JSON Prompt Builder Template (Ideogram 4.0)

The shipped `image_ideogram4_t2i` template takes a structured JSON prompt directly (injected as the `CLIPTextEncode` `text` string) and deliberately drops the official workflow's optional **LLM Prompt Builder** group. That group runs **Gemma 4** (`gemma4_e4b_it_fp8_scaled.safetensors`) locally with a system prompt that turns a short natural-language idea into schema-compliant Ideogram JSON, so you can prompt with a sentence instead of hand-writing the spec.

Look into shipping this as an alternative template (e.g. `image_ideogram4_t2i_gemma`) that wires the Gemma 4 builder ahead of the image pipeline. Worth doing since Gemma 4 already runs locally on the Mac here.

Open questions to resolve before building:

- **Node availability** — the builder uses `TextGenerate`, `StringConcatenate`, `PrimitiveStringMultiline`, `CLIPLoader` (`type: "ideogram4"`). Confirm `TextGenerate` is a real core node and how it's invoked in API format (it's the LLM-inference node, not a normal encoder).
- **Two-stage vs single graph** — either chain Gemma→Ideogram in one workflow (one `queue_prompt`, but the intermediate JSON is invisible to the user), or keep them as separate templates so the JSON can be previewed/edited between stages. The original workflow keeps them separate (the builder is a bypassed subgraph feeding a PreviewAny).
- **`model_requirements`** — add the `gemma4_e4b_it_fp8_scaled.safetensors` text-encoder download (HF: `Comfy-Org/gemma-4`), which the current template omits since it doesn't load Gemma.
- **bbox axis order** — the bundled Gemma system prompt instructs `[x_min, y_min, x_max, y_max]`, which is transposed relative to the model's actual `[y_min, x_min, y_max, x_max]` (see the `image_ideogram4_t2i` notes). Fix the system prompt before reusing it, or layouts will come out mirrored across the diagonal.
