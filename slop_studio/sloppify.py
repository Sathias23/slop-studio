"""CLIP-based prompt sloppifier — swaps words for semantically similar tokens."""

import math
import re
from random import sample

from slop_studio.errors import terminal_error

# Lazy-loaded globals
_clip_model = None
_tokenizer = None


def _ensure_clip():
    """Load CLIP model and tokenizer on first use. Raises on missing deps."""
    global _clip_model, _tokenizer
    if _clip_model is not None:
        return

    try:
        import clip
        import torch
    except ImportError as exc:
        raise ImportError(
            "The sloppify feature requires 'torch' and 'clip'. "
            "Install them with: pip install torch git+https://github.com/openai/CLIP.git"
        ) from exc

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model, _ = clip.load("ViT-B/32", device=device)
    model.eval().float().requires_grad_(False)
    _clip_model = model
    _tokenizer = clip.simple_tokenizer.SimpleTokenizer()


def _extract_words(text: str) -> list[str]:
    """Extract eligible words (alphabetic, length > 2) from text."""
    cleaned = re.sub(r"[^a-zA-Z ]", "", text)
    return [w for w in cleaned.split() if len(w) > 2]


def _synonymise_word(word: str, top_k: int) -> str:
    """Replace a single word with a random CLIP-similar token."""
    import torch

    tokens = _tokenizer.encode(word)
    parts = []
    for token in tokens:
        target_emb = _clip_model.token_embedding.weight[token, None].detach()
        similarity = torch.cosine_similarity(target_emb, _clip_model.token_embedding.weight.detach(), -1)
        top = torch.topk(similarity, top_k + 1, -1, True, True)
        # Skip index 0 (the token itself)
        candidates = top.indices[1:]
        shuffled = sample(range(candidates.shape[0]), candidates.shape[0])
        replacement = _tokenizer.decode([candidates[shuffled[0]].item()])
        parts.append(replacement)

    result = "".join(parts).strip()
    # Strip non-ASCII
    return result.encode("ascii", "ignore").decode("ascii")


async def sloppify_prompt(prompt: str, top_k: int = 8, synonym_ratio: int = 100) -> dict:
    """Sloppify a prompt by replacing words with CLIP-similar tokens.

    Args:
        prompt: Text prompt to sloppify.
        top_k: Number of nearest CLIP neighbours to sample from (1-32).
        synonym_ratio: Percentage of eligible words to replace (0-100).

    Returns:
        Dict with sloppified_prompt, original_prompt, and words_replaced.
    """
    if not prompt or not prompt.strip():
        return terminal_error("invalid_inputs", "Prompt is empty")

    if not 1 <= top_k <= 32:
        return terminal_error("invalid_inputs", f"top_k must be between 1 and 32, got {top_k}")

    if not 0 <= synonym_ratio <= 100:
        return terminal_error(
            "invalid_inputs",
            f"synonym_ratio must be between 0 and 100, got {synonym_ratio}",
        )

    if synonym_ratio == 0:
        return {
            "status": "success",
            "sloppified_prompt": prompt,
            "original_prompt": prompt,
            "words_replaced": 0,
        }

    try:
        _ensure_clip()
    except ImportError as exc:
        return terminal_error("missing_dependency", str(exc))

    words = _extract_words(prompt)
    if not words:
        return {
            "status": "success",
            "sloppified_prompt": prompt,
            "original_prompt": prompt,
            "words_replaced": 0,
        }

    num_to_replace = max(1, math.ceil(len(words) * synonym_ratio / 100))
    num_to_replace = min(num_to_replace, len(words))

    targets = sample(words, num_to_replace)

    new_prompt = prompt
    replaced = 0
    for word in targets:
        synonym = _synonymise_word(word, top_k)
        if synonym:
            new_prompt = re.sub(r"\b" + re.escape(word) + r"\b", synonym, new_prompt, count=1)
            replaced += 1

    return {
        "status": "success",
        "sloppified_prompt": new_prompt,
        "original_prompt": prompt,
        "words_replaced": replaced,
    }
