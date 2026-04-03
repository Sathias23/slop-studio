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
