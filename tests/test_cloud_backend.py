"""Unit tests for slop_studio.backends.cloud.

Story 6.4. Covers the five ABC methods, the three-shape error parser,
the submit error-code mapping, and the security-critical auth-strip
behavior of ``view()`` on a 302 redirect.

Mocking strategy: respx for every HTTP-exercising test; unit helpers
for the pure-function parser. All async tests run under
``@pytest.mark.anyio``.
"""

import logging
from pathlib import Path

import httpx
import pytest
import respx
from PIL import Image

from slop_studio.backends.cloud import (
    CloudBackend,
    _mask_key,
    _parse_error_body,
    _parse_json_safe,
)

CLOUD_BASE_URL = "https://cloud.comfy.org"
TEST_API_KEY = "comfyui-test1234567"


@pytest.fixture
def cloud_backend() -> CloudBackend:
    return CloudBackend(api_key=TEST_API_KEY, base_url=CLOUD_BASE_URL)


@pytest.fixture
def image_file(tmp_path: Path) -> Path:
    """Create a valid 4x4 PNG file for upload tests."""
    path = tmp_path / "probe.png"
    img = Image.new("RGB", (4, 4), color="red")
    img.save(path)
    return path


# ---------------------------------------------------------------------------
# Pure helpers — no HTTP.
# ---------------------------------------------------------------------------


def test_mask_key_short_form():
    assert _mask_key("comfyui-abcdef12345") == "comfyui***"


def test_mask_key_short_key_fully_masked():
    # Keys <= 7 chars must not expose any characters — key[:7] would return the full key.
    assert _mask_key("abc") == "***"
    assert _mask_key("1234567") == "***"


def test_mask_key_empty_returns_empty():
    assert _mask_key("") == ""


def test_parse_json_safe_returns_none_on_non_json():
    resp = httpx.Response(200, content=b"not json at all")
    assert _parse_json_safe(resp) is None


def test_parse_json_safe_returns_none_on_list_body():
    resp = httpx.Response(200, json=[1, 2, 3])
    assert _parse_json_safe(resp) is None


def test_parse_json_safe_returns_dict():
    resp = httpx.Response(200, json={"a": 1})
    assert _parse_json_safe(resp) == {"a": 1}


# Three-shape error-body parser — AC #17.


def test_parse_error_body_shape_1_terse_message():
    body = {"message": "Unmarshal type error: cannot parse"}
    code, msg = _parse_error_body(body)
    assert code == "UNKNOWN"
    assert msg == "Unmarshal type error: cannot parse"


def test_parse_error_body_shape_2_nested_error_object():
    body = {"error": {"type": "VALIDATION_ERROR", "message": "Invalid workflow", "details": "..."}}
    code, msg = _parse_error_body(body)
    assert code == "VALIDATION_ERROR"
    assert msg == "Invalid workflow"


def test_parse_error_body_shape_3_code_plus_message():
    body = {"code": "NOT_FOUND", "message": "Job not found"}
    code, msg = _parse_error_body(body)
    assert code == "NOT_FOUND"
    assert msg == "Job not found"


def test_parse_error_body_none_returns_unknown():
    code, msg = _parse_error_body(None)
    assert code == "UNKNOWN"
    assert msg == ""


# ---------------------------------------------------------------------------
# submit — AC #2, #16.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@respx.mock
async def test_submit_success_returns_prompt_id(cloud_backend):
    respx.post(f"{CLOUD_BASE_URL}/api/prompt").mock(
        return_value=httpx.Response(200, json={"prompt_id": "abc-uuid-123", "node_errors": {}})
    )
    result = await cloud_backend.submit({"1": {"class_type": "CLIPTextEncode"}})
    assert result == {"status": "success", "prompt_id": "abc-uuid-123"}


@pytest.mark.anyio
@respx.mock
async def test_submit_no_prefix_at_backend_layer(cloud_backend):
    # Router is responsible for prefixing — backend returns native id only.
    respx.post(f"{CLOUD_BASE_URL}/api/prompt").mock(
        return_value=httpx.Response(200, json={"prompt_id": "native-xyz", "node_errors": {}})
    )
    result = await cloud_backend.submit({})
    assert result["prompt_id"] == "native-xyz"
    assert not result["prompt_id"].startswith("cloud:")


@pytest.mark.anyio
@respx.mock
async def test_submit_node_errors_non_empty_returns_invalid_workflow(cloud_backend):
    respx.post(f"{CLOUD_BASE_URL}/api/prompt").mock(
        return_value=httpx.Response(
            200,
            json={
                "prompt_id": "never-ran",
                "node_errors": {"1": {"class_type": "UnetLoaderGGUF", "errors": "not supported"}},
            },
        )
    )
    result = await cloud_backend.submit({})
    assert result["status"] == "error"
    assert result["error_type"] == "invalid_workflow"
    assert result["retry_suggested"] is False


@pytest.mark.anyio
@respx.mock
async def test_submit_non_json_response_returns_invalid_workflow(cloud_backend):
    respx.post(f"{CLOUD_BASE_URL}/api/prompt").mock(return_value=httpx.Response(200, content=b"<html>not json</html>"))
    result = await cloud_backend.submit({})
    assert result["status"] == "error"
    assert result["error_type"] == "invalid_workflow"


@pytest.mark.anyio
@respx.mock
async def test_submit_missing_prompt_id_returns_invalid_workflow(cloud_backend):
    respx.post(f"{CLOUD_BASE_URL}/api/prompt").mock(return_value=httpx.Response(200, json={"some_other_field": "x"}))
    result = await cloud_backend.submit({})
    assert result["status"] == "error"
    assert result["error_type"] == "invalid_workflow"


@pytest.mark.anyio
@respx.mock
async def test_submit_transport_error_returns_transient(cloud_backend):
    respx.post(f"{CLOUD_BASE_URL}/api/prompt").mock(side_effect=httpx.ConnectError("connection refused"))
    result = await cloud_backend.submit({})
    assert result["status"] == "error"
    assert result["error_type"] == "unreachable"
    assert result["retry_suggested"] is True


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize(
    "status_code,expected_retry",
    [
        (400, False),
        (401, False),
        (402, False),
        (403, False),
        (413, False),
        (415, False),
        (422, False),
        (429, True),
        (500, True),
        (503, True),
    ],
)
async def test_submit_error_codes_return_appropriate_dict(cloud_backend, status_code, expected_retry):
    respx.post(f"{CLOUD_BASE_URL}/api/prompt").mock(
        return_value=httpx.Response(status_code, json={"message": f"status {status_code}"})
    )
    result = await cloud_backend.submit({})
    assert result["status"] == "error"
    assert result["retry_suggested"] is expected_retry


@pytest.mark.anyio
@respx.mock
async def test_submit_401_message_masks_api_key(cloud_backend):
    respx.post(f"{CLOUD_BASE_URL}/api/prompt").mock(return_value=httpx.Response(401, json={"message": "Bad key"}))
    result = await cloud_backend.submit({})
    assert TEST_API_KEY not in result["error"]
    assert "comfyui***" in result["error"]


# ---------------------------------------------------------------------------
# status + history — AC #3, #4.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize(
    "cloud_status,expected_state",
    [
        ("pending", "pending"),
        ("waiting_to_dispatch", "pending"),
        ("in_progress", "running"),
        ("executing", "running"),
    ],
)
async def test_status_maps_non_terminal_states(cloud_backend, cloud_status, expected_state):
    respx.get(f"{CLOUD_BASE_URL}/api/job/abc/status").mock(
        return_value=httpx.Response(200, json={"status": cloud_status})
    )
    result = await cloud_backend.status("abc")
    assert result == {"state": expected_state}


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize("cloud_status", ["success", "completed"])
async def test_status_completed_synthesizes_outputs_via_history(cloud_backend, cloud_status):
    respx.get(f"{CLOUD_BASE_URL}/api/job/abc/status").mock(
        return_value=httpx.Response(200, json={"status": cloud_status})
    )
    respx.get(f"{CLOUD_BASE_URL}/api/history/abc").mock(
        return_value=httpx.Response(
            200,
            json={
                "history": [
                    {
                        "prompt_id": "abc",
                        "outputs": {"9": {"images": [{"filename": "out.png"}]}},
                    }
                ]
            },
        )
    )
    result = await cloud_backend.status("abc")
    assert result["state"] == "completed"
    assert result["outputs"] == {"9": {"images": [{"filename": "out.png"}]}}


@pytest.mark.anyio
@respx.mock
async def test_status_error_puts_error_message_in_error_field(cloud_backend):
    respx.get(f"{CLOUD_BASE_URL}/api/job/abc/status").mock(
        return_value=httpx.Response(200, json={"status": "error", "error_message": "boom"})
    )
    result = await cloud_backend.status("abc")
    assert result == {"state": "failed", "error": "boom"}


@pytest.mark.anyio
@respx.mock
async def test_status_cancelled_maps_to_failed(cloud_backend):
    respx.get(f"{CLOUD_BASE_URL}/api/job/abc/status").mock(
        return_value=httpx.Response(200, json={"status": "cancelled"})
    )
    result = await cloud_backend.status("abc")
    assert result == {"state": "failed", "error": "cancelled"}


@pytest.mark.anyio
@respx.mock
async def test_status_propagates_http_error(cloud_backend):
    respx.get(f"{CLOUD_BASE_URL}/api/job/abc/status").mock(return_value=httpx.Response(503, content=b"unavailable"))
    with pytest.raises(httpx.HTTPStatusError):
        await cloud_backend.status("abc")


@pytest.mark.anyio
@respx.mock
async def test_history_returns_entry_outputs(cloud_backend):
    respx.get(f"{CLOUD_BASE_URL}/api/history/abc").mock(
        return_value=httpx.Response(
            200,
            json={"history": [{"prompt_id": "abc", "outputs": {"5": {"images": [{"filename": "x.png"}]}}}]},
        )
    )
    outputs = await cloud_backend.history("abc")
    assert outputs == {"5": {"images": [{"filename": "x.png"}]}}


@pytest.mark.anyio
@respx.mock
async def test_history_returns_empty_when_no_matching_entry(cloud_backend):
    respx.get(f"{CLOUD_BASE_URL}/api/history/abc").mock(return_value=httpx.Response(200, json={"history": []}))
    outputs = await cloud_backend.history("abc")
    assert outputs == {}


@pytest.mark.anyio
@respx.mock
async def test_history_returns_empty_when_entry_missing_outputs(cloud_backend):
    respx.get(f"{CLOUD_BASE_URL}/api/history/abc").mock(
        return_value=httpx.Response(200, json={"history": [{"prompt_id": "abc"}]})
    )
    outputs = await cloud_backend.history("abc")
    assert outputs == {}


# ---------------------------------------------------------------------------
# view — AC #5 (security-critical auth-strip on redirect).
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@respx.mock
async def test_view_strips_auth_on_302_redirect_to_storage(cloud_backend):
    """Security canary: X-API-Key MUST NOT leak to storage.googleapis.com.

    If someone simplifies view() to use follow_redirects=True on the
    auth'd client, this test catches it.
    """
    respx.get(f"{CLOUD_BASE_URL}/api/view").mock(
        return_value=httpx.Response(302, headers={"Location": "https://storage.googleapis.com/bucket/x.png"})
    )
    redirect_route = respx.get("https://storage.googleapis.com/bucket/x.png").mock(
        return_value=httpx.Response(200, content=b"fake-image-bytes")
    )

    result = await cloud_backend.view("x.png")

    assert result == b"fake-image-bytes"
    assert redirect_route.called
    assert "X-API-Key" not in redirect_route.calls[0].request.headers
    assert "Authorization" not in redirect_route.calls[0].request.headers


@pytest.mark.anyio
@respx.mock
async def test_view_200_direct_bytes_without_redirect(cloud_backend):
    respx.get(f"{CLOUD_BASE_URL}/api/view").mock(return_value=httpx.Response(200, content=b"direct-bytes"))
    result = await cloud_backend.view("x.png")
    assert result == b"direct-bytes"


@pytest.mark.anyio
async def test_view_rejects_empty_filename(cloud_backend):
    with pytest.raises(ValueError):
        await cloud_backend.view("")


@pytest.mark.anyio
async def test_view_rejects_dot_filename(cloud_backend):
    with pytest.raises(ValueError):
        await cloud_backend.view(".")


@pytest.mark.anyio
async def test_view_rejects_dotdot_filename(cloud_backend):
    with pytest.raises(ValueError):
        await cloud_backend.view("..")


@pytest.mark.anyio
@respx.mock
async def test_view_sanitizes_path_traversal(cloud_backend):
    # Path components must be stripped before reaching the origin.
    respx.get(f"{CLOUD_BASE_URL}/api/view").mock(return_value=httpx.Response(200, content=b"sanitized"))
    await cloud_backend.view("../../../etc/passwd")
    # filename should have been reduced to "passwd"
    assert "../" not in str(respx.calls[0].request.url)
    assert "/etc" not in str(respx.calls[0].request.url)


@pytest.mark.anyio
@respx.mock
async def test_view_propagates_non_redirect_error(cloud_backend):
    respx.get(f"{CLOUD_BASE_URL}/api/view").mock(return_value=httpx.Response(404, content=b"not found"))
    with pytest.raises(httpx.HTTPStatusError):
        await cloud_backend.view("missing.png")


# ---------------------------------------------------------------------------
# upload_asset — AC #6.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@respx.mock
async def test_upload_asset_returns_asset_hash_on_201(cloud_backend, image_file):
    respx.post(f"{CLOUD_BASE_URL}/api/assets").mock(
        return_value=httpx.Response(201, json={"asset_hash": "abc123hash.png", "name": "probe.png"})
    )
    result = await cloud_backend.upload_asset(str(image_file))
    assert result == "abc123hash.png"


@pytest.mark.anyio
@respx.mock
async def test_upload_asset_accepts_200_dedup_hit(cloud_backend, image_file):
    respx.post(f"{CLOUD_BASE_URL}/api/assets").mock(
        return_value=httpx.Response(200, json={"asset_hash": "dedup-hit.png"})
    )
    result = await cloud_backend.upload_asset(str(image_file))
    assert result == "dedup-hit.png"


@pytest.mark.anyio
async def test_upload_asset_missing_file_raises_value_error(cloud_backend):
    with pytest.raises(ValueError, match="not found"):
        await cloud_backend.upload_asset("/tmp/definitely-does-not-exist-abc.png")


@pytest.mark.anyio
async def test_upload_asset_invalid_image_raises_value_error(cloud_backend, tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not a real PNG")
    with pytest.raises(ValueError, match="not a valid image"):
        await cloud_backend.upload_asset(str(bad))


@pytest.mark.anyio
@respx.mock
async def test_upload_asset_missing_asset_hash_raises(cloud_backend, image_file):
    respx.post(f"{CLOUD_BASE_URL}/api/assets").mock(return_value=httpx.Response(201, json={"name": "probe.png"}))
    with pytest.raises(ValueError, match="asset_hash"):
        await cloud_backend.upload_asset(str(image_file))


@pytest.mark.anyio
@respx.mock
async def test_upload_asset_non_json_response_raises_value_error(cloud_backend, image_file):
    respx.post(f"{CLOUD_BASE_URL}/api/assets").mock(return_value=httpx.Response(201, content=b"<not-json>"))
    with pytest.raises(ValueError, match="non-JSON"):
        await cloud_backend.upload_asset(str(image_file))


@pytest.mark.anyio
@respx.mock
async def test_upload_asset_propagates_http_error(cloud_backend, image_file):
    respx.post(f"{CLOUD_BASE_URL}/api/assets").mock(return_value=httpx.Response(500, content=b"server error"))
    with pytest.raises(httpx.HTTPStatusError):
        await cloud_backend.upload_asset(str(image_file))


# ---------------------------------------------------------------------------
# API key masking regression — AC #19.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@respx.mock
async def test_api_key_never_leaks_in_error_dicts_or_logs(cloud_backend, caplog):
    """Grep all returned error messages AND caplog output for the raw key."""
    caplog.set_level(logging.DEBUG, logger="slop_studio.backends.cloud")

    # Exercise every error-emitting code path we can reach from tests.
    respx.post(f"{CLOUD_BASE_URL}/api/prompt").mock(return_value=httpx.Response(401, json={"message": "unauthorized"}))
    r1 = await cloud_backend.submit({})

    respx.post(f"{CLOUD_BASE_URL}/api/prompt").mock(return_value=httpx.Response(500, content=b"boom"))
    r2 = await cloud_backend.submit({})

    for result in (r1, r2):
        assert TEST_API_KEY not in result.get("error", "")
    assert TEST_API_KEY not in caplog.text
