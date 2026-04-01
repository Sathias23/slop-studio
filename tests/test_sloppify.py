"""Tests for slop_studio.sloppify module."""

import math
from unittest.mock import MagicMock, patch

import pytest

import slop_studio.sloppify as sloppify_mod
from slop_studio.sloppify import _extract_words, sloppify_prompt


# ---------------------------------------------------------------------------
# _extract_words
# ---------------------------------------------------------------------------


class TestExtractWords:
    def test_basic(self):
        assert _extract_words("a big red dog") == ["big", "red", "dog"]

    def test_strips_punctuation_and_numbers(self):
        assert _extract_words("hello, world! 42 cats") == ["hello", "world", "cats"]

    def test_short_words_excluded(self):
        assert _extract_words("I am a go to it") == []

    def test_empty_string(self):
        assert _extract_words("") == []


# ---------------------------------------------------------------------------
# sloppify_prompt — validation & edge cases (no CLIP needed)
# ---------------------------------------------------------------------------


class TestSloppifyValidation:
    @pytest.mark.anyio
    async def test_empty_prompt(self):
        result = await sloppify_prompt("", top_k=5, synonym_ratio=100)
        assert result["status"] == "error"
        assert "empty" in result["error"].lower()

    @pytest.mark.anyio
    async def test_whitespace_prompt(self):
        result = await sloppify_prompt("   ", top_k=5, synonym_ratio=100)
        assert result["status"] == "error"

    @pytest.mark.anyio
    async def test_top_k_too_low(self):
        result = await sloppify_prompt("hello world test", top_k=0, synonym_ratio=100)
        assert result["status"] == "error"
        assert "top_k" in result["error"]

    @pytest.mark.anyio
    async def test_top_k_too_high(self):
        result = await sloppify_prompt("hello world test", top_k=33, synonym_ratio=100)
        assert result["status"] == "error"

    @pytest.mark.anyio
    async def test_synonym_ratio_negative(self):
        result = await sloppify_prompt("hello world test", top_k=5, synonym_ratio=-1)
        assert result["status"] == "error"
        assert "synonym_ratio" in result["error"]

    @pytest.mark.anyio
    async def test_synonym_ratio_over_100(self):
        result = await sloppify_prompt("hello world test", top_k=5, synonym_ratio=150)
        assert result["status"] == "error"

    @pytest.mark.anyio
    async def test_synonym_ratio_zero_returns_original(self):
        result = await sloppify_prompt(
            "a sunset over mountains", top_k=5, synonym_ratio=0
        )
        assert result["status"] == "success"
        assert result["sloppified_prompt"] == "a sunset over mountains"
        assert result["original_prompt"] == "a sunset over mountains"
        assert result["words_replaced"] == 0


# ---------------------------------------------------------------------------
# sloppify_prompt — missing dependency
# ---------------------------------------------------------------------------


class TestSloppifyMissingDep:
    @pytest.mark.anyio
    async def test_missing_clip_returns_error(self):
        # Reset lazy globals so _ensure_clip runs
        sloppify_mod._clip_model = None
        sloppify_mod._tokenizer = None

        def fake_ensure():
            raise ImportError("torch and clip not installed")

        with patch.object(sloppify_mod, "_ensure_clip", side_effect=fake_ensure):
            result = await sloppify_prompt(
                "a sunset over mountains", top_k=5, synonym_ratio=100
            )
        assert result["status"] == "error"
        assert result["error_type"] == "missing_dependency"


# ---------------------------------------------------------------------------
# sloppify_prompt — with mocked CLIP
# ---------------------------------------------------------------------------


def _mock_synonymise(word, top_k):
    """Deterministic mock that prepends 'syn_' to the word."""
    return f"syn_{word}"


class TestSloppifyWithMockedCLIP:
    @pytest.fixture(autouse=True)
    def setup_mock(self):
        # Mock _ensure_clip to be a no-op and _synonymise_word to be deterministic
        self._patches = [
            patch.object(sloppify_mod, "_ensure_clip"),
            patch.object(sloppify_mod, "_synonymise_word", side_effect=_mock_synonymise),
        ]
        for p in self._patches:
            p.start()
        yield
        for p in self._patches:
            p.stop()

    @pytest.mark.anyio
    async def test_happy_path_all_words(self):
        result = await sloppify_prompt(
            "a sunset over mountains", top_k=5, synonym_ratio=100
        )
        assert result["status"] == "success"
        assert result["original_prompt"] == "a sunset over mountains"
        assert result["sloppified_prompt"] != "a sunset over mountains"
        assert result["words_replaced"] > 0

    @pytest.mark.anyio
    async def test_partial_replacement(self):
        prompt = "the quick brown fox jumps over the lazy dog"
        result = await sloppify_prompt(prompt, top_k=3, synonym_ratio=50)
        assert result["status"] == "success"
        eligible = _extract_words(prompt)
        expected_count = max(1, math.ceil(len(eligible) * 50 / 100))
        assert result["words_replaced"] <= expected_count

    @pytest.mark.anyio
    async def test_single_word(self):
        result = await sloppify_prompt("cat", top_k=5, synonym_ratio=100)
        assert result["status"] == "success"
        assert result["words_replaced"] == 1

    @pytest.mark.anyio
    async def test_no_eligible_words(self):
        result = await sloppify_prompt("a, I; 42", top_k=5, synonym_ratio=100)
        assert result["status"] == "success"
        assert result["words_replaced"] == 0
        assert result["sloppified_prompt"] == "a, I; 42"

    @pytest.mark.anyio
    async def test_preserves_original(self):
        original = "hello world testing"
        result = await sloppify_prompt(original, top_k=5, synonym_ratio=100)
        assert result["original_prompt"] == original

    @pytest.mark.anyio
    async def test_substring_not_corrupted(self):
        """Replacing 'cat' should not corrupt 'catastrophe'."""
        result = await sloppify_prompt(
            "catastrophe of a cat", top_k=5, synonym_ratio=100
        )
        assert result["status"] == "success"
        # 'cat' should be replaced with 'syn_cat', not inside 'catastrophe'
        assert "syn_catastrophe" in result["sloppified_prompt"]
        assert "syn_cat" in result["sloppified_prompt"]


class TestSloppifyEmptySynonym:
    @pytest.fixture(autouse=True)
    def setup_mock(self):
        self._patches = [
            patch.object(sloppify_mod, "_ensure_clip"),
            patch.object(
                sloppify_mod, "_synonymise_word", return_value=""
            ),
        ]
        for p in self._patches:
            p.start()
        yield
        for p in self._patches:
            p.stop()

    @pytest.mark.anyio
    async def test_empty_synonym_preserves_word(self):
        result = await sloppify_prompt("hello world testing", top_k=5, synonym_ratio=100)
        assert result["status"] == "success"
        assert result["sloppified_prompt"] == "hello world testing"
        assert result["words_replaced"] == 0
