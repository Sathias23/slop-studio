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
import logging
import os
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

import slop_studio.backends.local
import slop_studio.backends.router as router
import slop_studio.comfyui
import slop_studio.config
from slop_studio.backends.local import LocalBackend


@pytest.fixture(autouse=True)
def _isolate_cloud_config(monkeypatch):
    """Most router tests assume cloud is UNCONFIGURED. The lazy resolver now
    reads the live credentials.json on every lookup, so isolate the developer's
    real key from these tests by default. Tests that need cloud configured
    override ``get_comfy_cloud_api_key`` themselves via respx / monkeypatch."""
    monkeypatch.setattr("slop_studio.backends.router.get_comfy_cloud_api_key", lambda: "")
    router._BACKENDS.pop("cloud", None)
    yield
    router._BACKENDS.pop("cloud", None)


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
    assert result["error_type"] == "invalid_inputs"
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


# Minimal valid 1x1 green PNG served by the mocked /view endpoint. Hardcoded
# (rather than built via PIL) so importing this test module stays cheap even
# when the e2e tests aren't selected.
E2E_IMAGE_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f"
    b"\x00\x00\x00\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


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


# ===========================================================================
# Story 6.4 — CloudBackend registration + routing.
# ===========================================================================


CLOUD_BASE_URL = "https://cloud.comfy.org"
CLOUD_TEST_KEY = "comfyui-test1234567"


@pytest.fixture
def cloud_registered(monkeypatch):
    """Register CloudBackend in the router for the duration of a test.

    Inserts a ``CloudBackend`` directly into ``_BACKENDS`` rather than
    reloading the module — reloading the router re-imports
    ``LocalBackend`` from ``backends.local``, which conftest cycles on
    every teardown. A reload-on-teardown here would leave the router
    holding a stale ``LocalBackend`` class and break subsequent
    ``isinstance(..., LocalBackend)`` checks in Story 6.3 tests.

    Also sets the env vars so code paths that re-read them see the same
    configuration; tests that specifically exercise the env-flag
    registration reload the router themselves.
    """
    from slop_studio.backends.cloud import CloudBackend

    monkeypatch.setenv("COMFY_CLOUD_API_KEY", CLOUD_TEST_KEY)
    monkeypatch.setenv("COMFY_CLOUD_URL", CLOUD_BASE_URL)
    # Shadow the autouse _isolate_cloud_config patch: tests using this fixture
    # opt into a configured cloud, so the lazy resolver must see the test key.
    monkeypatch.setattr("slop_studio.backends.router.get_comfy_cloud_api_key", lambda: CLOUD_TEST_KEY)
    router._BACKENDS["cloud"] = CloudBackend(api_key=CLOUD_TEST_KEY, base_url=CLOUD_BASE_URL)
    try:
        yield router
    finally:
        router._BACKENDS.pop("cloud", None)


class TestLazyCloudResolver:
    """Cloud backend registration is lazy — runtime credential changes must
    be picked up without a process restart. The autouse _isolate_cloud_config
    fixture starts each test with an empty key; tests then mutate the
    key-getter and assert the registry reflects the change on next lookup."""

    def test_empty_key_returns_none_and_no_registration(self):
        assert router._resolve_cloud_backend() is None
        assert "cloud" not in router._BACKENDS

    def test_key_added_after_import_registers_cloud(self, monkeypatch):
        # Start unconfigured (autouse fixture).
        assert "cloud" not in router._BACKENDS
        # User runs `slop-studio auth --comfy-cloud`: credentials.json now
        # returns a key. The next lookup must register cloud.
        monkeypatch.setattr("slop_studio.backends.router.get_comfy_cloud_api_key", lambda: CLOUD_TEST_KEY)
        backend = router.get_backend("cloud")
        assert backend is not None
        assert backend.name == "cloud"
        assert router._BACKENDS["cloud"] is backend

    def test_second_lookup_reuses_cached_instance_when_key_unchanged(self, monkeypatch):
        monkeypatch.setattr("slop_studio.backends.router.get_comfy_cloud_api_key", lambda: CLOUD_TEST_KEY)
        first = router.get_backend("cloud")
        second = router.get_backend("cloud")
        assert first is second

    def test_key_rotation_rebuilds_backend(self, monkeypatch):
        monkeypatch.setattr("slop_studio.backends.router.get_comfy_cloud_api_key", lambda: CLOUD_TEST_KEY)
        first = router.get_backend("cloud")
        # User re-runs `auth` with a different key.
        monkeypatch.setattr("slop_studio.backends.router.get_comfy_cloud_api_key", lambda: "comfyui-NEWKEY-rotated")
        second = router.get_backend("cloud")
        assert first is not second
        assert second._api_key == "comfyui-NEWKEY-rotated"

    def test_key_removed_unregisters_cloud(self, monkeypatch):
        monkeypatch.setattr("slop_studio.backends.router.get_comfy_cloud_api_key", lambda: CLOUD_TEST_KEY)
        router.get_backend("cloud")
        assert "cloud" in router._BACKENDS
        # User deletes the credentials.json block AND unsets the env var.
        monkeypatch.setattr("slop_studio.backends.router.get_comfy_cloud_api_key", lambda: "")
        assert router._resolve_cloud_backend() is None
        assert "cloud" not in router._BACKENDS

    @pytest.mark.anyio
    async def test_route_submission_auth_failed_flips_to_success_after_key_add(self, monkeypatch, tmp_path):
        """Regression against the original bug: `route_submission` returned
        `auth_failed` on a cloud template when the key wasn't configured at
        import time, and stayed that way even after the user ran `auth`. The
        lazy resolver fixes this — the same call must succeed once the key
        is configured."""
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        _write_template(templates_dir, "cloud_tmpl", backend="cloud")
        monkeypatch.setattr(router, "TEMPLATES_DIR", str(templates_dir))

        # Step 1: no key → auth_failed (no HTTP call issued).
        result = await router.route_submission("cloud_tmpl", {"prompt": "hi"})
        assert result["status"] == "error"
        assert result["error_type"] == "auth_failed"

        # Step 2: user adds the key mid-session. The next call's chosen
        # backend path is `cloud`, and `_resolve_cloud_backend` picks up the
        # new key — we stop at the backend resolution and assert it's no
        # longer the `auth_failed` early-return branch.
        monkeypatch.setattr("slop_studio.backends.router.get_comfy_cloud_api_key", lambda: CLOUD_TEST_KEY)

        async def fake_prepare(_backend, _tmpl, _inputs, _aspect):
            return {"status": "success", "prompt_id": "nid-ok"}

        monkeypatch.setattr(router, "_prepare_and_submit", fake_prepare)
        result = await router.route_submission("cloud_tmpl", {"prompt": "hi"})
        assert result == {"status": "success", "prompt_id": "cloud:nid-ok"}


def test_cloud_registered_when_env_flag_set(monkeypatch):
    # AC #8 — env-flag-driven registration happens at router import time.
    # Story 6.5: router reads config.COMFY_CLOUD_URL / DEFAULT_BACKEND at
    # import time, so config must reload BEFORE router.
    snapshot_local = router._BACKENDS["local"]
    monkeypatch.setenv("COMFY_CLOUD_API_KEY", CLOUD_TEST_KEY)
    monkeypatch.setenv("COMFY_CLOUD_URL", CLOUD_BASE_URL)
    try:
        importlib.reload(slop_studio.config)
        importlib.reload(router)
        # Lazy resolver populates _BACKENDS on first lookup.
        cloud_backend = router.get_backend("cloud")
        assert cloud_backend.name == "cloud"
        assert "cloud" in router._BACKENDS
    finally:
        monkeypatch.delenv("COMFY_CLOUD_API_KEY", raising=False)
        monkeypatch.delenv("COMFY_CLOUD_URL", raising=False)
        # Restore the pre-reload LocalBackend instance so subsequent tests
        # that use isinstance(LocalBackend) continue to work.
        router._BACKENDS.clear()
        router._BACKENDS["local"] = snapshot_local


def test_cloud_not_registered_without_env_flag():
    # AC #8 — without the env var, the registry stays local-only.
    assert "cloud" not in router._BACKENDS


def test_route_for_prompt_id_round_trips_cloud_when_registered(cloud_registered):
    # AC #9 — "cloud:<id>" resolves without ValueError once registered.
    backend, native = cloud_registered.route_for_prompt_id("cloud:abc-123")
    assert backend.name == "cloud"
    assert native == "abc-123"


@pytest.mark.anyio
async def test_route_submission_cloud_without_env_flag_returns_terminal_error():
    # Story 6.5 AC #6 — override="cloud" with no registration → auth_failed
    # with guidance naming both credential surfaces.
    result = await router.route_submission("tmpl", {}, backend_override="cloud")
    assert result["status"] == "error"
    assert result["error_type"] == "auth_failed"
    assert "COMFY_CLOUD_API_KEY" in result["error"]
    assert "credentials.json" in result["error"]


@pytest.mark.anyio
async def test_route_submission_unknown_backend_override_returns_terminal_error():
    result = await router.route_submission("tmpl", {}, backend_override="martian")
    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "martian" in result["error"]


@pytest.mark.anyio
@respx.mock
async def test_route_submission_emits_cloud_prefix_with_override(cloud_registered, tmp_path, monkeypatch):
    # AC #10, #12 — cloud-routed submission emits "cloud:<native>" on success.
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "cloud_tmpl.json").write_text(
        json.dumps({"6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}}}),
        encoding="utf-8",
    )
    (templates_dir / "cloud_tmpl.meta.json").write_text(
        json.dumps(
            {
                "name": "cloud_tmpl",
                "inputs": {
                    "prompt": {
                        "node_id": "6",
                        "field": "text",
                        "type": "required",
                    }
                },
                "aspect_ratios": {},
                "resolution_nodes": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cloud_registered, "TEMPLATES_DIR", str(templates_dir))

    respx.post(f"{CLOUD_BASE_URL}/api/prompt").mock(
        return_value=httpx.Response(200, json={"prompt_id": "cloud-uuid-xyz", "node_errors": {}})
    )

    result = await cloud_registered.route_submission("cloud_tmpl", {"prompt": "hi"}, backend_override="cloud")

    assert result == {"status": "success", "prompt_id": "cloud:cloud-uuid-xyz"}


@pytest.mark.anyio
async def test_cloud_submission_does_not_invoke_ensure_ready(cloud_registered, monkeypatch):
    # AC #11, NFR-C6 canary — cloud path MUST NOT await ensure_ready.
    lifecycle_manager = AsyncMock()

    cloud_backend = cloud_registered._BACKENDS["cloud"]
    mock_submit = AsyncMock(return_value={"status": "success", "prompt_id": "xyz"})
    monkeypatch.setattr(cloud_backend, "submit", mock_submit)

    # Avoid touching the template loader — short-circuit via _prepare_and_submit.
    async def fake_prepare(_backend, _tmpl, _inputs, _aspect):
        return await _backend.submit({})

    monkeypatch.setattr(cloud_registered, "_prepare_and_submit", fake_prepare)

    result = await cloud_registered.route_submission(
        "tmpl",
        {},
        backend_override="cloud",
        lifecycle_manager=lifecycle_manager,
    )

    assert result == {"status": "success", "prompt_id": "cloud:xyz"}
    assert lifecycle_manager.ensure_ready.await_count == 0


@pytest.mark.anyio
async def test_local_submission_calls_ensure_ready(monkeypatch):
    # AC #11 — router now owns the ensure_ready gate on the local path.
    mock_qp = AsyncMock(return_value={"status": "success", "prompt_id": "local-123"})
    monkeypatch.setattr(slop_studio.backends.local, "queue_prompt", mock_qp)

    lifecycle_manager = AsyncMock()
    lifecycle_manager.ensure_ready = AsyncMock(return_value=None)

    result = await router.route_submission("tmpl", {}, lifecycle_manager=lifecycle_manager)

    assert result["prompt_id"] == "local:local-123"
    lifecycle_manager.ensure_ready.assert_awaited_once()


@pytest.mark.anyio
async def test_local_submission_short_circuits_on_ensure_ready_error(monkeypatch):
    # AC #11 — ensure_ready error short-circuits before queue_prompt is called.
    mock_qp = AsyncMock()
    monkeypatch.setattr(slop_studio.backends.local, "queue_prompt", mock_qp)

    lifecycle_error = {"status": "error", "error_type": "unreachable", "error": "not ready", "retry_suggested": True}
    lifecycle_manager = AsyncMock()
    lifecycle_manager.ensure_ready = AsyncMock(return_value=lifecycle_error)

    result = await router.route_submission("tmpl", {}, lifecycle_manager=lifecycle_manager)

    assert result == lifecycle_error
    mock_qp.assert_not_awaited()


@pytest.mark.anyio
async def test_local_submission_without_lifecycle_manager_still_works(monkeypatch):
    # Regression — the kwarg is optional; legacy callers continue to work.
    mock_qp = AsyncMock(return_value={"status": "success", "prompt_id": "x"})
    monkeypatch.setattr(slop_studio.backends.local, "queue_prompt", mock_qp)

    result = await router.route_submission("tmpl", {})
    assert result["prompt_id"] == "local:x"


@pytest.mark.anyio
async def test_check_next_job_mixed_backend_batch_rejected(cloud_registered):
    # AC #13 scope caveat — mixed batches surface a clear error.
    result = await cloud_registered.check_next_job(["local:a", "cloud:b"])
    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "Mixed-backend" in result["error"]


@pytest.mark.anyio
async def test_check_next_job_reprefixes_cloud_ids(cloud_registered, monkeypatch):
    # AC #13, #14 — all-cloud batch routes to cloud fan-out, re-prefixes cloud:.
    cloud_backend = cloud_registered._BACKENDS["cloud"]

    async def fake_status(nid):
        return {"state": "completed", "outputs": {"9": {"images": [{"filename": "x.png"}]}}}

    monkeypatch.setattr(cloud_backend, "status", fake_status)

    result = await cloud_registered.check_next_job(["cloud:abc"], wait=0)

    assert result["status"] == "completed"
    assert result["completed"][0]["prompt_id"] == "cloud:abc"
    assert result["completed"][0]["outputs"] == {"9": {"images": [{"filename": "x.png"}]}}


@pytest.mark.anyio
async def test_check_next_job_cloud_timeout_reprefixes_remaining(cloud_registered, monkeypatch):
    # Remaining list also re-prefixes cloud:, not hardcoded local:.
    cloud_backend = cloud_registered._BACKENDS["cloud"]

    async def pending_status(nid):
        return {"state": "pending"}

    monkeypatch.setattr(cloud_backend, "status", pending_status)

    result = await cloud_registered.check_next_job(["cloud:abc"], wait=0)

    assert result["status"] == "completed"  # empty batch counts as "done polling"
    assert result["remaining"] == ["cloud:abc"]


@pytest.mark.anyio
@respx.mock
async def test_get_image_routes_to_cloud_backend(cloud_registered, tmp_path, monkeypatch):
    # AC #15 — prefixed cloud id resolves to CloudBackend, writes to OUTPUT_DIR.
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setattr(cloud_registered, "OUTPUT_DIR", str(output_dir))

    respx.get(f"{CLOUD_BASE_URL}/api/job/xyz/status").mock(return_value=httpx.Response(200, json={"status": "success"}))
    respx.get(f"{CLOUD_BASE_URL}/api/history_v2/xyz").mock(
        return_value=httpx.Response(
            200,
            json={"xyz": {"outputs": {"9": {"images": [{"filename": "cloud_out.png"}]}}, "status": "success"}},
        )
    )
    # /api/view returns direct bytes (no redirect) — simpler for this test.
    respx.get(f"{CLOUD_BASE_URL}/api/view").mock(return_value=httpx.Response(200, content=b"\x89PNGfake-bytes"))

    result = await cloud_registered.get_image("cloud:xyz")

    assert result["status"] == "success"
    assert result["prompt_id"] == "cloud:xyz"
    assert os.path.isabs(result["file_path"])
    assert os.path.exists(result["file_path"])


@pytest.mark.anyio
async def test_no_auto_fallback_on_cloud_submit_5xx(cloud_registered, monkeypatch):
    # AC #18, NFR-C5 — cloud failure surfaces verbatim; router never retries local.
    mock_local_qp = AsyncMock(return_value={"status": "success", "prompt_id": "should-not-happen"})
    monkeypatch.setattr(slop_studio.backends.local, "queue_prompt", mock_local_qp)

    cloud_backend = cloud_registered._BACKENDS["cloud"]
    mock_submit = AsyncMock(
        return_value={
            "status": "error",
            "error_type": "unreachable",
            "error": "Cloud returned 500 at https://cloud.comfy.org",
            "retry_suggested": True,
        }
    )
    monkeypatch.setattr(cloud_backend, "submit", mock_submit)

    async def fake_prepare(backend, _tmpl, _inputs, _aspect):
        return await backend.submit({})

    monkeypatch.setattr(cloud_registered, "_prepare_and_submit", fake_prepare)

    result = await cloud_registered.route_submission("tmpl", {}, backend_override="cloud")

    assert result["status"] == "error"
    assert result["error_type"] == "unreachable"
    mock_local_qp.assert_not_awaited()


# ===========================================================================
# Story 6.5 — Full config resolution: env → credentials.json → config.toml.
# ===========================================================================


def _write_credentials_json(tmp_path, data):
    config_dir = tmp_path / ".config" / "slop-studio"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "credentials.json").write_text(json.dumps(data))


def _reload_router_with(monkeypatch, tmp_path):
    """Reload config then router with Path.home()→tmp_path. Returns snapshot
    of the pre-reload LocalBackend so the caller can restore on teardown."""
    snapshot_local = router._BACKENDS["local"]
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    importlib.reload(slop_studio.config)
    importlib.reload(router)
    return snapshot_local


def _restore_router(snapshot_local):
    router._BACKENDS.clear()
    router._BACKENDS["local"] = snapshot_local
    router.DEFAULT_BACKEND_NAME = "local"


def test_cloud_registered_from_credentials_json(monkeypatch, tmp_path):
    # Story 6.5 AC #3, #5 — credentials.json fallback registers cloud.
    monkeypatch.delenv("COMFY_CLOUD_API_KEY", raising=False)
    _write_credentials_json(tmp_path, {"comfy_cloud": {"api_key": CLOUD_TEST_KEY}})
    snapshot = _reload_router_with(monkeypatch, tmp_path)
    try:
        # Lazy resolver populates _BACKENDS on first lookup.
        cloud_backend = router.get_backend("cloud")
        assert cloud_backend.name == "cloud"
        assert "cloud" in router._BACKENDS
    finally:
        _restore_router(snapshot)


@pytest.mark.anyio
async def test_route_submission_default_backend_cloud_routes_to_cloud(monkeypatch, tmp_path):
    # Story 6.5 AC #9 — DEFAULT_BACKEND=cloud + valid key → unprefixed
    # submissions dispatch to cloud.
    monkeypatch.setenv("COMFY_CLOUD_API_KEY", CLOUD_TEST_KEY)
    monkeypatch.setenv("COMFY_CLOUD_URL", CLOUD_BASE_URL)
    monkeypatch.setenv("SLOP_STUDIO_DEFAULT_BACKEND", "cloud")
    snapshot = _reload_router_with(monkeypatch, tmp_path)
    try:
        assert router.DEFAULT_BACKEND_NAME == "cloud"
        # Lazy resolver populates _BACKENDS on first lookup.
        cloud_backend = router.get_backend("cloud")

        async def fake_prepare(backend, _tmpl, _inputs, _aspect):
            return await backend.submit({})

        monkeypatch.setattr(router, "_prepare_and_submit", fake_prepare)
        mock_submit = AsyncMock(return_value={"status": "success", "prompt_id": "nid-1"})
        monkeypatch.setattr(cloud_backend, "submit", mock_submit)

        result = await router.route_submission("tmpl", {})
        assert result == {"status": "success", "prompt_id": "cloud:nid-1"}
    finally:
        _restore_router(snapshot)


@pytest.mark.anyio
async def test_route_submission_default_backend_cloud_without_key_returns_auth_failed(monkeypatch, tmp_path):
    # Story 6.5 AC #10 — DEFAULT_BACKEND=cloud but NO key → auth_failed, same
    # message as the explicit-override path.
    monkeypatch.delenv("COMFY_CLOUD_API_KEY", raising=False)
    monkeypatch.setenv("SLOP_STUDIO_DEFAULT_BACKEND", "cloud")
    snapshot = _reload_router_with(monkeypatch, tmp_path)
    try:
        assert router.DEFAULT_BACKEND_NAME == "cloud"
        assert "cloud" not in router._BACKENDS
        result = await router.route_submission("tmpl", {})
        assert result["status"] == "error"
        assert result["error_type"] == "auth_failed"
        assert "COMFY_CLOUD_API_KEY" in result["error"]
        assert "credentials.json" in result["error"]
        # Story 6.7: message now points to open_comfy_cloud_portal too.
        assert "open_comfy_cloud_portal" in result["error"]
    finally:
        _restore_router(snapshot)


@pytest.mark.anyio
@respx.mock
async def test_cloud_backend_uses_config_cloud_url(monkeypatch, tmp_path):
    # Story 6.5 AC #8 — COMFY_CLOUD_URL flows into CloudBackend.base_url.
    staging_url = "https://staging.example.com"
    monkeypatch.setenv("COMFY_CLOUD_API_KEY", CLOUD_TEST_KEY)
    monkeypatch.setenv("COMFY_CLOUD_URL", staging_url)
    snapshot = _reload_router_with(monkeypatch, tmp_path)
    try:
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        (templates_dir / "staging_tmpl.json").write_text(
            json.dumps({"6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}}}),
            encoding="utf-8",
        )
        (templates_dir / "staging_tmpl.meta.json").write_text(
            json.dumps(
                {
                    "name": "staging_tmpl",
                    "inputs": {"prompt": {"node_id": "6", "field": "text", "type": "required"}},
                    "aspect_ratios": {},
                    "resolution_nodes": [],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(router, "TEMPLATES_DIR", str(templates_dir))

        respx.post(f"{staging_url}/api/prompt").mock(
            return_value=httpx.Response(200, json={"prompt_id": "staging-xyz", "node_errors": {}})
        )

        result = await router.route_submission("staging_tmpl", {"prompt": "hi"}, backend_override="cloud")
        assert result == {"status": "success", "prompt_id": "cloud:staging-xyz"}
    finally:
        _restore_router(snapshot)


@pytest.mark.anyio
async def test_no_raw_api_key_in_router_logs_or_error_dicts(monkeypatch, tmp_path, caplog):
    # Story 6.5 AC #7 (NFR-C3) — raw key MUST NOT appear in router-level logs
    # or in any returned error dict along the cloud path.
    unique_key = "comfyui-DONOTLEAK-router-0123456789"
    monkeypatch.setenv("COMFY_CLOUD_API_KEY", unique_key)
    monkeypatch.setenv("COMFY_CLOUD_URL", CLOUD_BASE_URL)
    snapshot = _reload_router_with(monkeypatch, tmp_path)
    try:
        with caplog.at_level(logging.DEBUG):
            # Force an auth-failed path too by nuking the key and re-reloading.
            cloud_backend = router.get_backend("cloud")
            assert cloud_backend is not None

            async def fake_prepare(backend, _tmpl, _inputs, _aspect):
                return await backend.submit({})

            monkeypatch.setattr(router, "_prepare_and_submit", fake_prepare)
            mock_submit = AsyncMock(return_value={"status": "success", "prompt_id": "leak-test"})
            monkeypatch.setattr(cloud_backend, "submit", mock_submit)

            result = await router.route_submission("tmpl", {}, backend_override="cloud")
        assert result == {"status": "success", "prompt_id": "cloud:leak-test"}
        assert unique_key not in caplog.text
        for value in result.values():
            assert unique_key not in str(value)
    finally:
        _restore_router(snapshot)


# ===========================================================================
# Story 6.6 — Meta-driven backend resolution (backend field in .meta.json).
# ===========================================================================


def _write_template(templates_dir, name, *, backend=None):
    """Write a minimal .json + .meta.json pair for router-level tests."""
    (templates_dir / f"{name}.json").write_text(
        json.dumps({"6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}}}),
        encoding="utf-8",
    )
    meta = {
        "name": name,
        "model": "test-model",
        "description": "test",
        "inputs": {"prompt": {"node_id": "6", "field": "text", "type": "required"}},
        "aspect_ratios": {},
        "resolution_nodes": [],
    }
    if backend is not None:
        meta["backend"] = backend
    (templates_dir / f"{name}.meta.json").write_text(json.dumps(meta), encoding="utf-8")


@pytest.mark.anyio
async def test_route_submission_reads_template_backend_local(monkeypatch, tmp_path):
    # AC #7 — template "backend": "local" with no override routes local.
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    _write_template(templates_dir, "tpl_local", backend="local")
    monkeypatch.setattr(router, "TEMPLATES_DIR", str(templates_dir))

    mock_qp = AsyncMock(return_value={"status": "success", "prompt_id": "nid-local"})
    monkeypatch.setattr(slop_studio.backends.local, "queue_prompt", mock_qp)

    result = await router.route_submission("tpl_local", {"prompt": "hi"})

    assert result == {"status": "success", "prompt_id": "local:nid-local"}
    mock_qp.assert_awaited_once_with("tpl_local", {"prompt": "hi"}, None)


@pytest.mark.anyio
@respx.mock
async def test_route_submission_reads_template_backend_cloud(cloud_registered, tmp_path, monkeypatch):
    # AC #8 — template "backend": "cloud" routes to CloudBackend without
    # an explicit backend_override and emits a "cloud:<native>" prompt_id.
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    _write_template(templates_dir, "tpl_cloud", backend="cloud")
    monkeypatch.setattr(router, "TEMPLATES_DIR", str(templates_dir))

    local_qp_spy = AsyncMock()
    monkeypatch.setattr(slop_studio.backends.local, "queue_prompt", local_qp_spy)

    respx.post(f"{CLOUD_BASE_URL}/api/prompt").mock(
        return_value=httpx.Response(200, json={"prompt_id": "cloud-nid-1", "node_errors": {}})
    )

    result = await router.route_submission("tpl_cloud", {"prompt": "hi"})

    assert result == {"status": "success", "prompt_id": "cloud:cloud-nid-1"}
    local_qp_spy.assert_not_awaited()


@pytest.mark.anyio
async def test_route_submission_template_backend_cloud_without_registration_returns_auth_failed(monkeypatch, tmp_path):
    # AC #7, #9 cross-check — "backend": "cloud" but cloud not registered →
    # auth_failed with guidance (same message as Story 6.5 AC #6).
    monkeypatch.delenv("COMFY_CLOUD_API_KEY", raising=False)
    assert "cloud" not in router._BACKENDS
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    _write_template(templates_dir, "tpl_cloud_nokey", backend="cloud")
    monkeypatch.setattr(router, "TEMPLATES_DIR", str(templates_dir))

    result = await router.route_submission("tpl_cloud_nokey", {"prompt": "hi"})

    assert result["status"] == "error"
    assert result["error_type"] == "auth_failed"
    assert "COMFY_CLOUD_API_KEY" in result["error"]
    assert "credentials.json" in result["error"]


@pytest.mark.anyio
async def test_route_submission_template_backend_either_uses_default_local(monkeypatch, tmp_path):
    # AC #9 — "backend": "either" + DEFAULT_BACKEND=local → routes local.
    assert router.DEFAULT_BACKEND_NAME == "local"
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    _write_template(templates_dir, "tpl_either", backend="either")
    monkeypatch.setattr(router, "TEMPLATES_DIR", str(templates_dir))

    mock_qp = AsyncMock(return_value={"status": "success", "prompt_id": "nid-either"})
    monkeypatch.setattr(slop_studio.backends.local, "queue_prompt", mock_qp)

    result = await router.route_submission("tpl_either", {"prompt": "hi"})

    assert result == {"status": "success", "prompt_id": "local:nid-either"}


@pytest.mark.anyio
async def test_route_submission_template_backend_either_uses_default_cloud(monkeypatch, tmp_path):
    # AC #9 — "backend": "either" + DEFAULT_BACKEND=cloud (registered) → cloud.
    monkeypatch.setenv("COMFY_CLOUD_API_KEY", CLOUD_TEST_KEY)
    monkeypatch.setenv("COMFY_CLOUD_URL", CLOUD_BASE_URL)
    monkeypatch.setenv("SLOP_STUDIO_DEFAULT_BACKEND", "cloud")
    snapshot = _reload_router_with(monkeypatch, tmp_path)
    try:
        assert router.DEFAULT_BACKEND_NAME == "cloud"
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        _write_template(templates_dir, "tpl_either_cloud", backend="either")
        monkeypatch.setattr(router, "TEMPLATES_DIR", str(templates_dir))

        cloud_backend = router.get_backend("cloud")

        async def fake_prepare(backend, _tmpl, _inputs, _aspect):
            return await backend.submit({})

        monkeypatch.setattr(router, "_prepare_and_submit", fake_prepare)
        mock_submit = AsyncMock(return_value={"status": "success", "prompt_id": "cloud-nid-e"})
        monkeypatch.setattr(cloud_backend, "submit", mock_submit)

        result = await router.route_submission("tpl_either_cloud", {"prompt": "hi"})

        assert result == {"status": "success", "prompt_id": "cloud:cloud-nid-e"}
    finally:
        _restore_router(snapshot)


@pytest.mark.anyio
async def test_route_submission_template_backend_either_default_cloud_without_key_returns_auth_failed(
    monkeypatch, tmp_path
):
    # AC #9 — "backend": "either" + DEFAULT_BACKEND=cloud + NO key → auth_failed
    # (same path as Story 6.5 AC #10).
    monkeypatch.delenv("COMFY_CLOUD_API_KEY", raising=False)
    monkeypatch.setenv("SLOP_STUDIO_DEFAULT_BACKEND", "cloud")
    snapshot = _reload_router_with(monkeypatch, tmp_path)
    try:
        assert router.DEFAULT_BACKEND_NAME == "cloud"
        assert "cloud" not in router._BACKENDS
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        _write_template(templates_dir, "tpl_either_nokey", backend="either")
        monkeypatch.setattr(router, "TEMPLATES_DIR", str(templates_dir))

        result = await router.route_submission("tpl_either_nokey", {"prompt": "hi"})

        assert result["status"] == "error"
        assert result["error_type"] == "auth_failed"
        assert "COMFY_CLOUD_API_KEY" in result["error"]
        assert "credentials.json" in result["error"]
    finally:
        _restore_router(snapshot)


@pytest.mark.anyio
@respx.mock
async def test_route_submission_backend_override_beats_template_local(cloud_registered, tmp_path, monkeypatch):
    # AC #10 — template "backend": "local" + backend_override="cloud" → cloud.
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    _write_template(templates_dir, "tpl_local_forced_cloud", backend="local")
    monkeypatch.setattr(router, "TEMPLATES_DIR", str(templates_dir))

    respx.post(f"{CLOUD_BASE_URL}/api/prompt").mock(
        return_value=httpx.Response(200, json={"prompt_id": "cloud-override", "node_errors": {}})
    )

    result = await router.route_submission(
        "tpl_local_forced_cloud",
        {"prompt": "hi"},
        backend_override="cloud",
    )

    assert result == {"status": "success", "prompt_id": "cloud:cloud-override"}


@pytest.mark.anyio
async def test_route_submission_backend_override_beats_template_cloud(cloud_registered, tmp_path, monkeypatch):
    # AC #10 — template "backend": "cloud" + backend_override="local" → local.
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    _write_template(templates_dir, "tpl_cloud_forced_local", backend="cloud")
    monkeypatch.setattr(router, "TEMPLATES_DIR", str(templates_dir))

    mock_qp = AsyncMock(return_value={"status": "success", "prompt_id": "nid-forced-local"})
    monkeypatch.setattr(slop_studio.backends.local, "queue_prompt", mock_qp)

    result = await router.route_submission(
        "tpl_cloud_forced_local",
        {"prompt": "hi"},
        backend_override="local",
    )

    assert result == {"status": "success", "prompt_id": "local:nid-forced-local"}
    mock_qp.assert_awaited_once()


@pytest.mark.anyio
async def test_route_submission_template_no_backend_field_falls_through_to_default(monkeypatch, tmp_path):
    # AC #7 step 5, AC #17 canary — absent backend field falls through to
    # DEFAULT_BACKEND_NAME (preserves Story 6.5's user-default seam).
    assert router.DEFAULT_BACKEND_NAME == "local"
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    _write_template(templates_dir, "tpl_legacy")
    monkeypatch.setattr(router, "TEMPLATES_DIR", str(templates_dir))

    mock_qp = AsyncMock(return_value={"status": "success", "prompt_id": "nid-legacy"})
    monkeypatch.setattr(slop_studio.backends.local, "queue_prompt", mock_qp)

    result = await router.route_submission("tpl_legacy", {"prompt": "hi"})

    assert result == {"status": "success", "prompt_id": "local:nid-legacy"}


@pytest.mark.anyio
async def test_route_submission_malformed_template_meta_falls_through_to_default(monkeypatch, tmp_path):
    # AC #13 — malformed meta must not raise from route_submission; the
    # downstream local.queue_prompt surfaces a terminal error of its own.
    assert router.DEFAULT_BACKEND_NAME == "local"
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "tpl_garbage.meta.json").write_text("{not json", encoding="utf-8")
    (templates_dir / "tpl_garbage.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(router, "TEMPLATES_DIR", str(templates_dir))

    # Stub local.queue_prompt so we don't depend on its internal meta load
    # for assertion purposes — the focus of AC #13 is that the router
    # resolver itself does not raise on malformed meta.
    mock_qp = AsyncMock(return_value={"status": "success", "prompt_id": "nid-g"})
    monkeypatch.setattr(slop_studio.backends.local, "queue_prompt", mock_qp)

    result = await router.route_submission("tpl_garbage", {"prompt": "hi"})

    assert isinstance(result, dict)
    assert result == {"status": "success", "prompt_id": "local:nid-g"}


def test_read_template_backend_returns_none_for_missing_file(monkeypatch, tmp_path):
    # AC #14 — helper returns None on missing file (no exception).
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    monkeypatch.setattr(router, "TEMPLATES_DIR", str(templates_dir))

    assert router._read_template_backend("nonexistent") is None


def test_read_template_backend_returns_none_for_invalid_value(monkeypatch, tmp_path):
    # AC #14 — helper returns None when the backend value is not one of
    # the allowed strings (defense against hand-edited meta files).
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "tpl_handhack.meta.json").write_text(
        json.dumps({"name": "tpl_handhack", "backend": "klown"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(router, "TEMPLATES_DIR", str(templates_dir))

    assert router._read_template_backend("tpl_handhack") is None


def test_read_template_backend_returns_none_for_malformed_json(monkeypatch, tmp_path):
    # AC #14 — helper returns None on JSONDecodeError, no exception raised.
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "tpl_badjson.meta.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(router, "TEMPLATES_DIR", str(templates_dir))

    assert router._read_template_backend("tpl_badjson") is None


def test_read_template_backend_returns_valid_values(monkeypatch, tmp_path):
    # AC #14 — helper returns "local" / "cloud" / "either" verbatim when valid.
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    for value in ("local", "cloud", "either"):
        (templates_dir / f"tpl_{value}.meta.json").write_text(
            json.dumps({"name": f"tpl_{value}", "backend": value}),
            encoding="utf-8",
        )
    monkeypatch.setattr(router, "TEMPLATES_DIR", str(templates_dir))

    assert router._read_template_backend("tpl_local") == "local"
    assert router._read_template_backend("tpl_cloud") == "cloud"
    assert router._read_template_backend("tpl_either") == "either"


# ===========================================================================
# Story 6.7 — Refined error codes + backend tagging on error dicts.
# ===========================================================================


@pytest.mark.anyio
async def test_prepare_and_submit_template_not_found_tags_backend_cloud(tmp_path, monkeypatch, cloud_registered):
    # AC #22 — cloud-path template error carries backend="cloud".
    monkeypatch.setattr(router, "TEMPLATES_DIR", str(tmp_path))
    result = await router._prepare_and_submit(router._BACKENDS["cloud"], "nonexistent-template", {}, None)
    assert result["status"] == "error"
    assert result["backend"] == "cloud"


@pytest.mark.anyio
@respx.mock
async def test_check_next_job_cloud_transport_error_tags_backend_cloud(cloud_registered):
    # AC #22 — transport error in cloud polling tags backend="cloud".
    respx.get(f"{CLOUD_BASE_URL}/api/job/abc/status").mock(side_effect=httpx.ConnectError("boom"))
    result = await router._check_next_job_cloud(router._BACKENDS["cloud"], ["abc"], 0)
    assert result["status"] == "error"
    assert result["backend"] == "cloud"


@pytest.mark.anyio
@respx.mock
async def test_get_image_cloud_pending_state_tags_backend_cloud(cloud_registered):
    # AC #22 — pending state from cloud tags backend="cloud".
    respx.get(f"{CLOUD_BASE_URL}/api/job/abc/status").mock(return_value=httpx.Response(200, json={"status": "pending"}))
    result = await router._get_image_cloud(router._BACKENDS["cloud"], "abc", include_base64=False)
    assert result["error_type"] == "invalid_inputs"
    assert result["backend"] == "cloud"


@pytest.mark.anyio
@respx.mock
async def test_get_image_cloud_failed_state_tags_backend_cloud(cloud_registered):
    # AC #22 — failed state maps to generation_failed AND tags backend="cloud".
    respx.get(f"{CLOUD_BASE_URL}/api/job/abc/status").mock(
        return_value=httpx.Response(200, json={"status": "error", "error_message": "node crashed"})
    )
    result = await router._get_image_cloud(router._BACKENDS["cloud"], "abc", include_base64=False)
    assert result["error_type"] == "generation_failed"
    assert result["backend"] == "cloud"


@pytest.mark.anyio
async def test_route_submission_cloud_without_key_tags_backend_cloud(tmp_path, monkeypatch):
    # AC #23 — template declares cloud, but no key configured → auth_failed
    # with backend="cloud" tag (route_submission unregistered-cloud branch).
    # No cloud_registered fixture → cloud not in _BACKENDS.
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    _write_template(templates_dir, "cloud-only", backend="cloud")
    monkeypatch.setattr(router, "TEMPLATES_DIR", str(templates_dir))
    result = await router.route_submission("cloud-only", {"prompt": "hi"})
    assert result["error_type"] == "auth_failed"
    assert result["backend"] == "cloud"


# ---------------------------------------------------------------------------
# Regression: pre-fix, _check_next_job_cloud and _get_image_cloud mapped every
# HTTPStatusError (401/402/403/429/5xx) to transient_error("unreachable"),
# hiding auth/credit/quota failures behind a misleading "retry" hint. The
# router now delegates to CloudBackend.http_error_to_dict so the full Story
# 6.7 taxonomy applies on non-submit paths too.
# ---------------------------------------------------------------------------


class TestCloudNonSubmitErrorTaxonomy:
    @pytest.mark.anyio
    @respx.mock
    async def test_check_next_job_401_maps_to_auth_failed(self, cloud_registered):
        respx.get(f"{CLOUD_BASE_URL}/api/job/abc/status").mock(
            return_value=httpx.Response(401, json={"error": "bad key"})
        )
        result = await router._check_next_job_cloud(router._BACKENDS["cloud"], ["abc"], 0)
        assert result["status"] == "error"
        assert result["error_type"] == "auth_failed"
        assert result["backend"] == "cloud"
        assert result["retry_suggested"] is False

    @pytest.mark.anyio
    @respx.mock
    async def test_check_next_job_402_maps_to_no_credits(self, cloud_registered):
        respx.get(f"{CLOUD_BASE_URL}/api/job/abc/status").mock(
            return_value=httpx.Response(402, json={"error": "no credits"})
        )
        result = await router._check_next_job_cloud(router._BACKENDS["cloud"], ["abc"], 0)
        assert result["error_type"] == "no_credits"
        assert result["retry_suggested"] is False

    @pytest.mark.anyio
    @respx.mock
    async def test_check_next_job_403_maps_to_account_issue(self, cloud_registered):
        respx.get(f"{CLOUD_BASE_URL}/api/job/abc/status").mock(
            return_value=httpx.Response(403, json={"error": "subscription expired"})
        )
        result = await router._check_next_job_cloud(router._BACKENDS["cloud"], ["abc"], 0)
        assert result["error_type"] == "account_issue"
        assert result["retry_suggested"] is False

    @pytest.mark.anyio
    @respx.mock
    async def test_check_next_job_429_maps_to_rate_limited(self, cloud_registered):
        respx.get(f"{CLOUD_BASE_URL}/api/job/abc/status").mock(
            return_value=httpx.Response(429, json={"error": "slow down"})
        )
        result = await router._check_next_job_cloud(router._BACKENDS["cloud"], ["abc"], 0)
        assert result["error_type"] == "rate_limited"

    @pytest.mark.anyio
    @respx.mock
    async def test_check_next_job_5xx_still_transient(self, cloud_registered):
        # 5xx stays transient per the existing contract.
        respx.get(f"{CLOUD_BASE_URL}/api/job/abc/status").mock(
            return_value=httpx.Response(503, json={"error": "maintenance"})
        )
        result = await router._check_next_job_cloud(router._BACKENDS["cloud"], ["abc"], 0)
        assert result["error_type"] == "unreachable"
        assert result["retry_suggested"] is True

    @pytest.mark.anyio
    @respx.mock
    async def test_get_image_401_on_view_maps_to_auth_failed(self, cloud_registered, tmp_path, monkeypatch):
        # Status succeeds → proceeds to fetch image → /api/view returns 401.
        monkeypatch.setattr(router, "OUTPUT_DIR", str(tmp_path))
        respx.get(f"{CLOUD_BASE_URL}/api/job/abc/status").mock(
            return_value=httpx.Response(
                200,
                json={"status": "success", "outputs": {"9": {"images": [{"filename": "out.png", "type": "output"}]}}},
            )
        )
        respx.get(f"{CLOUD_BASE_URL}/api/history_v2/abc").mock(
            return_value=httpx.Response(
                200,
                json={
                    "abc": {
                        "outputs": {"9": {"images": [{"filename": "out.png", "type": "output"}]}},
                        "status": "success",
                    }
                },
            )
        )
        respx.get(f"{CLOUD_BASE_URL}/api/view").mock(return_value=httpx.Response(401, json={"error": "bad key"}))

        result = await router._get_image_cloud(router._BACKENDS["cloud"], "abc", include_base64=False)
        assert result["error_type"] == "auth_failed"
        assert result["backend"] == "cloud"
        assert result["retry_suggested"] is False

    @pytest.mark.anyio
    @respx.mock
    async def test_get_image_401_on_status_maps_to_auth_failed(self, cloud_registered, tmp_path, monkeypatch):
        # Status returns 401 → should route through http_error_to_dict, not
        # fall through to the transient "unreachable" bucket.
        monkeypatch.setattr(router, "OUTPUT_DIR", str(tmp_path))
        respx.get(f"{CLOUD_BASE_URL}/api/job/abc/status").mock(
            return_value=httpx.Response(401, json={"error": "bad key"})
        )

        result = await router._get_image_cloud(router._BACKENDS["cloud"], "abc", include_base64=False)
        assert result["error_type"] == "auth_failed"
        assert result["backend"] == "cloud"
        assert result["retry_suggested"] is False
