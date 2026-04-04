"""Tests for slop_studio.bluesky — Bluesky posting integration."""

import io
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from slop_studio import bluesky


def _mock_embed_models():
    """Patch atproto models to bypass Pydantic validation in tests."""
    mock_image_cls = MagicMock()
    mock_main_cls = MagicMock()
    mock_embed = MagicMock()
    mock_embed.AppBskyEmbedImages = MagicMock()
    mock_embed.AppBskyEmbedImages.Image = mock_image_cls
    mock_embed.AppBskyEmbedImages.Main = mock_main_cls
    return mock_embed


@pytest.fixture
def tmp_image(tmp_path):
    """Create a small valid PNG file for testing."""
    from PIL import Image

    img = Image.new("RGB", (64, 64), color="red")
    path = tmp_path / "test.png"
    img.save(path, format="PNG")
    return str(path)


@pytest.fixture
def large_image(tmp_path):
    """Create a >1MB PNG file for compression testing."""
    from PIL import Image

    # Create a >1MB file by saving as uncompressed BMP then reading back
    img = Image.new("RGB", (1024, 1024), color="blue")
    path = tmp_path / "large.bmp"
    img.save(path, format="BMP")
    assert path.stat().st_size > bluesky.BLOB_LIMIT
    return str(path)


# --- Missing config ---


@pytest.mark.anyio
async def test_missing_credentials(tmp_image):
    with patch.object(bluesky, "BSKY_HANDLE", ""), patch.object(
        bluesky, "BSKY_APP_PASSWORD", ""
    ):
        result = await bluesky.post_image(tmp_image, "hello", "alt")
    assert result["status"] == "error"
    assert result["error_type"] == "missing_config"
    assert result["retry_suggested"] is False
    assert "BSKY_HANDLE" in result["error"]


@pytest.mark.anyio
async def test_missing_handle_only(tmp_image):
    with patch.object(bluesky, "BSKY_HANDLE", ""), patch.object(
        bluesky, "BSKY_APP_PASSWORD", "secret"
    ):
        result = await bluesky.post_image(tmp_image, "hello", "alt")
    assert result["error_type"] == "missing_config"


@pytest.mark.anyio
async def test_missing_password_only(tmp_image):
    with patch.object(bluesky, "BSKY_HANDLE", "user.bsky.social"), patch.object(
        bluesky, "BSKY_APP_PASSWORD", ""
    ):
        result = await bluesky.post_image(tmp_image, "hello", "alt")
    assert result["error_type"] == "missing_config"


# --- File not found ---


@pytest.mark.anyio
async def test_file_not_found():
    with patch.object(bluesky, "BSKY_HANDLE", "user.bsky.social"), patch.object(
        bluesky, "BSKY_APP_PASSWORD", "secret"
    ):
        result = await bluesky.post_image("/nonexistent/image.png", "hello", "alt")
    assert result["status"] == "error"
    assert result["error_type"] == "file_not_found"


# --- Text too long ---


@pytest.mark.anyio
async def test_text_too_long(tmp_image):
    with patch.object(bluesky, "BSKY_HANDLE", "user.bsky.social"), patch.object(
        bluesky, "BSKY_APP_PASSWORD", "secret"
    ):
        long_text = "x" * 301
        result = await bluesky.post_image(tmp_image, long_text, "alt")
    assert result["status"] == "error"
    assert result["error_type"] == "validation_failed"
    assert "301" in result["error"]


@pytest.mark.anyio
async def test_text_plus_tags_too_long(tmp_image):
    with patch.object(bluesky, "BSKY_HANDLE", "user.bsky.social"), patch.object(
        bluesky, "BSKY_APP_PASSWORD", "secret"
    ):
        # 290 chars of text + tags should exceed 300
        text = "x" * 290
        result = await bluesky.post_image(
            tmp_image, text, "alt", tags=["aiart", "comfyui"]
        )
    assert result["error_type"] == "validation_failed"


# --- Auth failure ---


@pytest.mark.anyio
async def test_auth_failure(tmp_image):
    from atproto_client.exceptions import UnauthorizedError

    mock_client = AsyncMock()
    mock_client.login = AsyncMock(side_effect=UnauthorizedError())

    with patch.object(bluesky, "BSKY_HANDLE", "user.bsky.social"), patch.object(
        bluesky, "BSKY_APP_PASSWORD", "bad-password"
    ), patch("slop_studio.bluesky.AsyncClient", return_value=mock_client):
        result = await bluesky.post_image(tmp_image, "hello", "alt")
    assert result["status"] == "error"
    assert result["error_type"] == "auth_failed"


# --- Network errors ---


@pytest.mark.anyio
async def test_blob_upload_network_error(tmp_image):
    from atproto_client.exceptions import NetworkError

    mock_client = AsyncMock()
    mock_client.login = AsyncMock()
    mock_client.upload_blob = AsyncMock(side_effect=NetworkError())

    with patch.object(bluesky, "BSKY_HANDLE", "user.bsky.social"), patch.object(
        bluesky, "BSKY_APP_PASSWORD", "secret"
    ), patch("slop_studio.bluesky.AsyncClient", return_value=mock_client):
        result = await bluesky.post_image(tmp_image, "hello", "alt")
    assert result["status"] == "error"
    assert result["error_type"] == "network_error"
    assert result["retry_suggested"] is True


@pytest.mark.anyio
async def test_blob_upload_bad_request(tmp_image):
    from atproto_client.exceptions import BadRequestError

    mock_client = AsyncMock()
    mock_client.login = AsyncMock()
    mock_client.upload_blob = AsyncMock(
        side_effect=BadRequestError(MagicMock(content=b"bad"))
    )

    with patch.object(bluesky, "BSKY_HANDLE", "user.bsky.social"), patch.object(
        bluesky, "BSKY_APP_PASSWORD", "secret"
    ), patch("slop_studio.bluesky.AsyncClient", return_value=mock_client):
        result = await bluesky.post_image(tmp_image, "hello", "alt")
    assert result["status"] == "error"
    assert result["error_type"] == "blob_upload_failed"
    assert result["retry_suggested"] is False


@pytest.mark.anyio
async def test_post_network_error(tmp_image):
    from atproto_client.exceptions import NetworkError

    mock_blob = MagicMock()
    mock_blob.blob = MagicMock()
    mock_client = AsyncMock()
    mock_client.login = AsyncMock()
    mock_client.upload_blob = AsyncMock(return_value=mock_blob)
    mock_client.send_post = AsyncMock(side_effect=NetworkError())

    with patch.object(bluesky, "BSKY_HANDLE", "user.bsky.social"), patch.object(
        bluesky, "BSKY_APP_PASSWORD", "secret"
    ), patch("slop_studio.bluesky.AsyncClient", return_value=mock_client), patch(
        "slop_studio.bluesky.models", _mock_embed_models()
    ):
        result = await bluesky.post_image(tmp_image, "hello", "alt")
    assert result["status"] == "error"
    assert result["error_type"] == "network_error"
    assert result["retry_suggested"] is True


# --- Happy path ---


@pytest.mark.anyio
async def test_happy_path(tmp_image):
    mock_blob = MagicMock()
    mock_blob.blob = MagicMock()
    mock_post = MagicMock()
    mock_post.uri = "at://did:plc:abc123/app.bsky.feed.post/xyz"
    mock_post.cid = "bafy123"

    mock_client = AsyncMock()
    mock_client.login = AsyncMock()
    mock_client.upload_blob = AsyncMock(return_value=mock_blob)
    mock_client.send_post = AsyncMock(return_value=mock_post)

    with patch.object(bluesky, "BSKY_HANDLE", "user.bsky.social"), patch.object(
        bluesky, "BSKY_APP_PASSWORD", "secret"
    ), patch("slop_studio.bluesky.AsyncClient", return_value=mock_client), patch(
        "slop_studio.bluesky.models", _mock_embed_models()
    ):
        result = await bluesky.post_image(tmp_image, "hello world", "alt text")

    assert result["status"] == "success"
    assert result["uri"] == "at://did:plc:abc123/app.bsky.feed.post/xyz"
    assert result["cid"] == "bafy123"

    # Verify send_post was called with embed
    call_args = mock_client.send_post.call_args
    assert call_args.kwargs["embed"] is not None


@pytest.mark.anyio
async def test_happy_path_with_tags(tmp_image):
    mock_blob = MagicMock()
    mock_blob.blob = MagicMock()
    mock_post = MagicMock()
    mock_post.uri = "at://did:plc:abc123/app.bsky.feed.post/xyz"
    mock_post.cid = "bafy123"

    mock_client = AsyncMock()
    mock_client.login = AsyncMock()
    mock_client.upload_blob = AsyncMock(return_value=mock_blob)
    mock_client.send_post = AsyncMock(return_value=mock_post)

    with patch.object(bluesky, "BSKY_HANDLE", "user.bsky.social"), patch.object(
        bluesky, "BSKY_APP_PASSWORD", "secret"
    ), patch("slop_studio.bluesky.AsyncClient", return_value=mock_client), patch(
        "slop_studio.bluesky.models", _mock_embed_models()
    ):
        result = await bluesky.post_image(
            tmp_image, "hello", "alt", tags=["aiart", "comfyui"]
        )

    assert result["status"] == "success"


# --- Compression ---


@pytest.mark.anyio
async def test_large_image_compressed(large_image):
    mock_blob = MagicMock()
    mock_blob.blob = MagicMock()
    mock_post = MagicMock()
    mock_post.uri = "at://did:plc:abc123/app.bsky.feed.post/xyz"
    mock_post.cid = "bafy123"

    mock_client = AsyncMock()
    mock_client.login = AsyncMock()
    mock_client.upload_blob = AsyncMock(return_value=mock_blob)
    mock_client.send_post = AsyncMock(return_value=mock_post)

    with patch.object(bluesky, "BSKY_HANDLE", "user.bsky.social"), patch.object(
        bluesky, "BSKY_APP_PASSWORD", "secret"
    ), patch("slop_studio.bluesky.AsyncClient", return_value=mock_client), patch(
        "slop_studio.bluesky.models", _mock_embed_models()
    ):
        result = await bluesky.post_image(large_image, "large image", "alt")

    assert result["status"] == "success"
    # Verify the uploaded data was compressed (< 1MB)
    uploaded_data = mock_client.upload_blob.call_args[0][0]
    assert len(uploaded_data) <= bluesky.BLOB_LIMIT


# --- TextBuilder unit tests ---


def test_build_post_text_no_tags():
    tb = bluesky._build_post_text("hello world")
    assert tb.build_text() == "hello world"


def test_build_post_text_with_tags():
    tb = bluesky._build_post_text("hello", tags=["aiart", "comfyui"])
    text = tb.build_text()
    assert text == "hello\n\n#aiart #comfyui"
    # Facets should be present
    facets = tb.build_facets()
    assert len(facets) == 2


def test_build_post_text_empty_tags():
    tb = bluesky._build_post_text("hello", tags=[])
    assert tb.build_text() == "hello"


def test_build_post_text_sanitizes_tags():
    """Tags with # prefix, empty strings, and whitespace are cleaned."""
    tb = bluesky._build_post_text("hello", tags=["#aiart", "", "  ", "comfyui"])
    text = tb.build_text()
    assert text == "hello\n\n#aiart #comfyui"
    facets = tb.build_facets()
    assert len(facets) == 2


def test_build_post_text_all_invalid_tags():
    """If all tags are empty/whitespace, no tag section is added."""
    tb = bluesky._build_post_text("hello", tags=["", "  "])
    assert tb.build_text() == "hello"


# --- Error edge cases ---


@pytest.mark.anyio
async def test_read_permission_error(tmp_image):
    """Permission errors reading the file are handled gracefully."""
    os.chmod(tmp_image, 0o000)
    try:
        with patch.object(bluesky, "BSKY_HANDLE", "user.bsky.social"), patch.object(
            bluesky, "BSKY_APP_PASSWORD", "secret"
        ):
            result = await bluesky.post_image(tmp_image, "hello", "alt")
        assert result["status"] == "error"
        assert result["error_type"] == "file_not_found"
    finally:
        os.chmod(tmp_image, 0o644)


@pytest.mark.anyio
async def test_login_network_error(tmp_image):
    """Network errors during login are transient."""
    from atproto_client.exceptions import NetworkError

    mock_client = AsyncMock()
    mock_client.login = AsyncMock(side_effect=NetworkError())

    with patch.object(bluesky, "BSKY_HANDLE", "user.bsky.social"), patch.object(
        bluesky, "BSKY_APP_PASSWORD", "secret"
    ), patch("slop_studio.bluesky.AsyncClient", return_value=mock_client):
        result = await bluesky.post_image(tmp_image, "hello", "alt")
    assert result["status"] == "error"
    assert result["error_type"] == "network_error"
    assert result["retry_suggested"] is True


def test_compress_corrupt_image():
    """Corrupt image data returns None instead of crashing."""
    result = bluesky._compress_image(b"not an image at all")
    assert result is None


# --- Compression unit tests ---


def test_compress_small_image():
    """Small images that already fit should still compress successfully."""
    from PIL import Image

    img = Image.new("RGB", (64, 64), color="red")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    result = bluesky._compress_image(buf.getvalue())
    assert result is not None
    assert len(result) <= bluesky.BLOB_LIMIT


def test_compress_rgba_image():
    """RGBA images should be converted to RGB before compression."""
    from PIL import Image

    img = Image.new("RGBA", (64, 64), color=(255, 0, 0, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    result = bluesky._compress_image(buf.getvalue())
    assert result is not None
