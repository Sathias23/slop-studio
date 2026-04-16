"""Unit tests for slop_studio.backends.router.

Story 6.2 introduced the routing seam; Story 6.3 formalizes the prefix
contract (``"local:<native>"``) on both ingress and egress.

Mocks are applied at the ``slop_studio.backends.local.{queue_prompt,
check_next_job, get_image}`` level via ``monkeypatch.setattr`` — this
mirrors the delegation-assertion pattern used throughout test_server.py
and is faster than respx setup. End-to-end round-trip tests (which DO
use respx) live further down in this file.
"""

import importlib
import json
import os
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

import slop_studio.backends.local
import slop_studio.backends.router as router
import slop_studio.comfyui
import slop_studio.config
from slop_studio.backends.local import LocalBackend

# ---------------------------------------------------------------------------
# Story 6.2 regression tests (unchanged bodies — names adjusted where the
# behavior they describe has evolved in 6.3).
# ---------------------------------------------------------------------------


def test_route_for_prompt_id_returns_local_backend():
    backend, native_id = router.route_for_prompt_id("abc-123")
    assert isinstance(backend, LocalBackend)
    assert native_id == "abc-123"


def test_route_for_prompt_id_prefixed_returns_local_backend():
    # Story 6.2 accepted the prefix leniently; Story 6.3 strips it. The
    # stricter post-strip assertion lives in
    # test_route_for_prompt_id_strips_local_prefix below.
    backend, _native = router.route_for_prompt_id("local:abc-123")
    assert isinstance(backend, LocalBackend)


def test_get_backend_local_returns_same_instance():
    # AC #7 from Story 6.2 — module-level singleton registry.
    a = router.get_backend("local")
    b = router.get_backend("local")
    assert a is b
    assert isinstance(a, LocalBackend)


def test_get_backend_unknown_raises():
    with pytest.raises(ValueError, match="cloud"):
        router.get_backend("cloud")


@pytest.mark.anyio
async def test_route_submission_delegates_to_local_orchestrator(monkeypatch):
    mock_qp = AsyncMock(return_value={"status": "success", "prompt_id": "xyz"})
    monkeypatch.setattr(slop_studio.backends.local, "queue_prompt", mock_qp)

    result = await router.route_submission("tmpl", {"prompt": "hi"}, aspect_ratio="16:9")

    # Story 6.3: the router now prefixes the returned id. Delegation call
    # signature is unchanged — the prefix is applied to the output only.
    assert result == {"status": "success", "prompt_id": "local:xyz"}
    mock_qp.assert_awaited_once_with("tmpl", {"prompt": "hi"}, "16:9")


@pytest.mark.anyio
async def test_check_next_job_empty_list_returns_terminal_error():
    # AC #6 parity with backends.local.check_next_job — same error shape.
    result = await router.check_next_job([])
    assert result["status"] == "error"
    assert result["error_type"] == "invalid_input"
    assert result["retry_suggested"] is False


@pytest.mark.anyio
async def test_check_next_job_delegates_to_local_orchestrator(monkeypatch):
    mock_cnj = AsyncMock(return_value={"status": "completed", "completed": [], "failed": [], "remaining": []})
    monkeypatch.setattr(slop_studio.backends.local, "check_next_job", mock_cnj)

    result = await router.check_next_job(["abc-123", "def-456"], wait=10)

    mock_cnj.assert_awaited_once_with(["abc-123", "def-456"], 10)
    assert result["status"] == "completed"


@pytest.mark.anyio
async def test_get_image_delegates_to_local_orchestrator(monkeypatch):
    mock_gi = AsyncMock(return_value={"status": "success", "file_path": "/tmp/x.png", "prompt_id": "abc"})
    monkeypatch.setattr(slop_studio.backends.local, "get_image", mock_gi)

    result = await router.get_image("abc", include_base64=True)

    mock_gi.assert_awaited_once_with("abc", include_base64=True)
    assert result["file_path"] == "/tmp/x.png"
    # Story 6.3: output prompt_id is canonicalized to the prefixed form even
    # when the caller passed a bare native id.
    assert result["prompt_id"] == "local:abc"


# ---------------------------------------------------------------------------
# Story 6.3 — route_for_prompt_id prefix handling (AC #3, #4, #5).
# ---------------------------------------------------------------------------


def test_route_for_prompt_id_strips_local_prefix():
    backend, native_id = router.route_for_prompt_id("local:abc-123")
    assert isinstance(backend, LocalBackend)
    assert native_id == "abc-123"


def test_route_for_prompt_id_bare_id_resolves_to_local():
    backend, native_id = router.route_for_prompt_id("abc-123")
    assert isinstance(backend, LocalBackend)
    assert native_id == "abc-123"


def test_route_for_prompt_id_unknown_prefix_raises():
    with pytest.raises(ValueError, match="cloud"):
        router.route_for_prompt_id("cloud:abc-123")


def test_route_for_prompt_id_splits_on_first_colon_only():
    # Native ids may themselves contain colons (e.g. future composite ids).
    # ``split(":", 1)`` is the contract; this test locks it in.
    backend, native_id = router.route_for_prompt_id("local:abc:def")
    assert isinstance(backend, LocalBackend)
    assert native_id == "abc:def"


# ---------------------------------------------------------------------------
# Story 6.3 — route_submission prefix emission (AC #1, #2).
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_route_submission_emits_local_prefix(monkeypatch):
    mock_qp = AsyncMock(return_value={"status": "success", "prompt_id": "native-uuid-123"})
    monkeypatch.setattr(slop_studio.backends.local, "queue_prompt", mock_qp)

    result = await router.route_submission("tmpl", {"prompt": "hi"})

    assert result == {"status": "success", "prompt_id": "local:native-uuid-123"}


@pytest.mark.anyio
async def test_route_submission_passes_through_errors_unprefixed(monkeypatch):
    # Terminal errors carry no prompt_id — the router must NOT fabricate one.
    error_dict = {
        "status": "error",
        "error": "Template 'missing' not found",
        "error_type": "invalid_inputs",
        "retry_suggested": False,
    }
    mock_qp = AsyncMock(return_value=error_dict)
    monkeypatch.setattr(slop_studio.backends.local, "queue_prompt", mock_qp)

    result = await router.route_submission("missing", {})

    assert result == error_dict
    assert "prompt_id" not in result


# ---------------------------------------------------------------------------
# Story 6.3 — check_next_job strip/re-prefix + unknown-prefix handling
# (AC #6, #7, #9, #12).
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_check_next_job_strips_prefix_before_delegation(monkeypatch):
    mock_cnj = AsyncMock(return_value={"status": "completed", "completed": [], "failed": [], "remaining": []})
    monkeypatch.setattr(slop_studio.backends.local, "check_next_job", mock_cnj)

    await router.check_next_job(["local:abc-123", "local:def-456"], wait=5)

    # Delegation receives NATIVE ids — the prefix is stripped on the way in.
    mock_cnj.assert_awaited_once_with(["abc-123", "def-456"], 5)


@pytest.mark.anyio
async def test_check_next_job_reprefixes_response(monkeypatch):
    mock_cnj = AsyncMock(
        return_value={
            "status": "completed",
            "completed": [{"prompt_id": "abc-123", "outputs": {"9": {}}}],
            "failed": [],
            "remaining": ["def-456"],
        }
    )
    monkeypatch.setattr(slop_studio.backends.local, "check_next_job", mock_cnj)

    result = await router.check_next_job(["local:abc-123", "local:def-456"])

    assert result["completed"][0]["prompt_id"] == "local:abc-123"
    assert result["completed"][0]["outputs"] == {"9": {}}
    assert result["remaining"] == ["local:def-456"]


@pytest.mark.anyio
async def test_check_next_job_reprefixes_failed_entries(monkeypatch):
    mock_cnj = AsyncMock(
        return_value={
            "status": "completed",
            "completed": [],
            "failed": [{"prompt_id": "abc-123", "error": "boom"}],
            "remaining": [],
        }
    )
    monkeypatch.setattr(slop_studio.backends.local, "check_next_job", mock_cnj)

    result = await router.check_next_job(["local:abc-123"])

    assert result["failed"][0]["prompt_id"] == "local:abc-123"
    assert result["failed"][0]["error"] == "boom"


@pytest.mark.anyio
async def test_check_next_job_bare_id_accepted(monkeypatch):
    # Legacy callers holding a pre-6.3 bare id still work — no warning, no
    # error, and the output canonicalizes to the prefixed form.
    mock_cnj = AsyncMock(
        return_value={
            "status": "completed",
            "completed": [{"prompt_id": "abc-123", "outputs": {}}],
            "failed": [],
            "remaining": [],
        }
    )
    monkeypatch.setattr(slop_studio.backends.local, "check_next_job", mock_cnj)

    result = await router.check_next_job(["abc-123"])

    mock_cnj.assert_awaited_once_with(["abc-123"], 0)
    assert result["completed"][0]["prompt_id"] == "local:abc-123"


@pytest.mark.anyio
async def test_check_next_job_unknown_prefix_returns_terminal_error(monkeypatch):
    mock_cnj = AsyncMock()
    monkeypatch.setattr(slop_studio.backends.local, "check_next_job", mock_cnj)

    result = await router.check_next_job(["cloud:abc-123"])

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert result["retry_suggested"] is False
    assert "cloud" in result["error"]
    mock_cnj.assert_not_awaited()


@pytest.mark.anyio
async def test_check_next_job_passes_through_transient_errors(monkeypatch):
    # AC #2 pattern — error dicts carry no per-id fields, return verbatim.
    transient = {
        "status": "error",
        "error": "Cannot connect to ComfyUI",
        "error_type": "unreachable",
        "retry_suggested": True,
    }
    mock_cnj = AsyncMock(return_value=transient)
    monkeypatch.setattr(slop_studio.backends.local, "check_next_job", mock_cnj)

    result = await router.check_next_job(["local:abc-123"])

    assert result == transient


# ---------------------------------------------------------------------------
# Story 6.3 — get_image strip/re-prefix + unknown-prefix handling
# (AC #6, #8, #9, #12).
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_image_strips_prefix_before_delegation(monkeypatch):
    mock_gi = AsyncMock(return_value={"status": "success", "file_path": "/tmp/x.png", "prompt_id": "abc-123"})
    monkeypatch.setattr(slop_studio.backends.local, "get_image", mock_gi)

    await router.get_image("local:abc-123")

    mock_gi.assert_awaited_once_with("abc-123", include_base64=False)


@pytest.mark.anyio
async def test_get_image_reprefixes_response(monkeypatch):
    mock_gi = AsyncMock(return_value={"status": "success", "file_path": "/tmp/x.png", "prompt_id": "abc-123"})
    monkeypatch.setattr(slop_studio.backends.local, "get_image", mock_gi)

    result = await router.get_image("local:abc-123")

    assert result["prompt_id"] == "local:abc-123"
    assert result["file_path"] == "/tmp/x.png"
    assert result["status"] == "success"


@pytest.mark.anyio
async def test_get_image_bare_id_accepted_and_reprefixed(monkeypatch):
    mock_gi = AsyncMock(return_value={"status": "success", "file_path": "/tmp/x.png", "prompt_id": "abc-123"})
    monkeypatch.setattr(slop_studio.backends.local, "get_image", mock_gi)

    result = await router.get_image("abc-123")

    mock_gi.assert_awaited_once_with("abc-123", include_base64=False)
    assert result["prompt_id"] == "local:abc-123"


@pytest.mark.anyio
async def test_get_image_unknown_prefix_returns_terminal_error(monkeypatch):
    mock_gi = AsyncMock()
    monkeypatch.setattr(slop_studio.backends.local, "get_image", mock_gi)

    result = await router.get_image("cloud:abc-123")

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert result["retry_suggested"] is False
    assert "cloud" in result["error"]
    mock_gi.assert_not_awaited()


@pytest.mark.anyio
async def test_get_image_passes_through_error_dicts(monkeypatch):
    # terminal_error dicts from backends.local.get_image have no prompt_id
    # field — return verbatim.
    err = {
        "status": "error",
        "error": "Job abc-123 is still pending (queued, not started)",
        "error_type": "invalid_inputs",
        "retry_suggested": False,
    }
    mock_gi = AsyncMock(return_value=err)
    monkeypatch.setattr(slop_studio.backends.local, "get_image", mock_gi)

    result = await router.get_image("local:abc-123")

    assert result == err


# ---------------------------------------------------------------------------
# Story 6.3 — log / error-message prefix visibility (AC #9, Task 7).
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_prefix_appears_in_error_messages():
    # AC #9 — unknown-prefix errors echo the full prefixed form so incidents
    # are easy to triage by backend.
    result = await router.get_image("cloud:abc-123")

    assert result["status"] == "error"
    assert "cloud:abc-123" in result["error"]


@pytest.mark.anyio
async def test_prefix_visible_in_remaining_list_on_timeout(monkeypatch):
    # Timeout shape — remaining entries carry the prefix so a caller polling
    # again still has routable ids.
    mock_cnj = AsyncMock(
        return_value={
            "status": "waiting",
            "completed": [],
            "failed": [],
            "remaining": ["abc-123"],
        }
    )
    monkeypatch.setattr(slop_studio.backends.local, "check_next_job", mock_cnj)

    result = await router.check_next_job(["local:abc-123"])

    assert result["remaining"] == ["local:abc-123"]


# ===========================================================================
# Story 6.3 — Task 6: end-to-end round-trip tests with respx.
# ===========================================================================

COMFYUI_URL = "http://test-comfyui:8188"

# Minimal workflow that survives ``_inject_inputs`` — a single CLIPTextEncode
# node receives the "prompt" input. No seed node, no resolution node, so we
# avoid the extra moving parts.
E2E_WORKFLOW = {
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "", "clip": ["4", 1]},
    },
}

E2E_META = {
    "name": "e2e_template",
    "model": "test-model",
    "description": "E2E test template",
    "expected_duration": "1 second",
    "inputs": {
        "prompt": {
            "node_id": "6",
            "field": "text",
            "type": "required",
            "description": "Prompt text",
        }
    },
    "aspect_ratios": {},
    "resolution_nodes": [],
}

HISTORY_COMPLETED = {
    "abc-123": {
        "outputs": {"9": {"images": [{"filename": "e2e_output.png", "subfolder": "", "type": "output"}]}},
        "status": {"status_str": "success", "completed": True, "messages": []},
    }
}


def _make_fake_image_bytes() -> bytes:
    """Create a minimal valid PNG for testing."""
    import io

    from PIL import Image as PILImage

    img = PILImage.new("RGB", (32, 32), color=(0, 128, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


E2E_IMAGE_BYTES = _make_fake_image_bytes()


@pytest.fixture
def e2e_env(tmp_path, monkeypatch):
    """Set up an isolated template + output dir and reload config modules."""
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    (templates_dir / "e2e_template.json").write_text(json.dumps(E2E_WORKFLOW), encoding="utf-8")
    (templates_dir / "e2e_template.meta.json").write_text(json.dumps(E2E_META), encoding="utf-8")

    monkeypatch.setenv("SLOP_STUDIO_TEMPLATES_DIR", str(templates_dir))
    monkeypatch.setenv("COMFYUI_URL", COMFYUI_URL)
    importlib.reload(slop_studio.config)
    importlib.reload(slop_studio.comfyui)

    # comfyui's reload cascades into backends.local, which the router holds
    # via attribute access. Override OUTPUT_DIR on the freshly-reloaded module.
    monkeypatch.setattr(slop_studio.comfyui, "OUTPUT_DIR", str(output_dir))
    monkeypatch.setattr(slop_studio.backends.local, "OUTPUT_DIR", str(output_dir))

    return templates_dir, output_dir


@pytest.mark.anyio
@respx.mock
async def test_end_to_end_roundtrip_with_prefix(e2e_env):
    # AC #10 — submit → receive prefixed id → check_next_job → get_image,
    # with a regression guard that no ComfyUI HTTP call includes "local:".
    respx.post(f"{COMFYUI_URL}/prompt").mock(
        return_value=httpx.Response(200, json={"prompt_id": "abc-123", "number": 1, "node_errors": {}})
    )
    respx.get(f"{COMFYUI_URL}/history/abc-123").mock(return_value=httpx.Response(200, json=HISTORY_COMPLETED))
    respx.get(f"{COMFYUI_URL}/view").mock(return_value=httpx.Response(200, content=E2E_IMAGE_BYTES))

    # 1. Submit — returned id carries the "local:" prefix.
    submission = await router.route_submission("e2e_template", {"prompt": "hello"})
    assert submission["status"] == "success"
    assert submission["prompt_id"] == "local:abc-123"

    prefixed_id = submission["prompt_id"]

    # 2. Poll — prefixed id goes in, prefixed id comes out.
    poll = await router.check_next_job([prefixed_id], wait=0)
    assert poll["status"] == "completed"
    assert poll["completed"][0]["prompt_id"] == "local:abc-123"

    # 3. Fetch the image — response prompt_id re-prefixed.
    img = await router.get_image(prefixed_id)
    assert img["status"] == "success"
    assert img["prompt_id"] == "local:abc-123"
    assert os.path.isabs(img["file_path"])
    assert os.path.exists(img["file_path"])

    # Regression guard — no respx call URL carries the prefix.
    for call in respx.calls:
        assert "local:" not in str(call.request.url), f"HTTP call leaked the prefix into the URL: {call.request.url}"


@pytest.mark.anyio
@respx.mock
async def test_end_to_end_roundtrip_legacy_bare_id(e2e_env):
    # AC #11 — a caller holding a pre-6.3 bare id still routes correctly.
    # Same ComfyUI HTTP calls, same response shape, no warning.
    respx.get(f"{COMFYUI_URL}/history/abc-123").mock(return_value=httpx.Response(200, json=HISTORY_COMPLETED))
    respx.get(f"{COMFYUI_URL}/view").mock(return_value=httpx.Response(200, content=E2E_IMAGE_BYTES))

    poll = await router.check_next_job(["abc-123"], wait=0)
    assert poll["status"] == "completed"
    # Output is canonicalized even though input was bare.
    assert poll["completed"][0]["prompt_id"] == "local:abc-123"

    img = await router.get_image("abc-123")
    assert img["status"] == "success"
    assert img["prompt_id"] == "local:abc-123"

    for call in respx.calls:
        assert "local:" not in str(call.request.url), f"HTTP call leaked the prefix into the URL: {call.request.url}"
