"""Unit tests for slop_studio.backends.router (Story 6.2).

These tests verify routing-seam behavior without hitting the network.
Mocks are applied at the ``slop_studio.backends.local.{queue_prompt,
check_next_job, get_image}`` level via ``monkeypatch.setattr`` — this
mirrors the delegation-assertion pattern used throughout test_server.py
and is faster than respx setup.
"""

from unittest.mock import AsyncMock

import pytest

import slop_studio.backends.local
import slop_studio.backends.router as router
from slop_studio.backends.local import LocalBackend


def test_route_for_prompt_id_returns_local_backend():
    backend, native_id = router.route_for_prompt_id("abc-123")
    assert isinstance(backend, LocalBackend)
    assert native_id == "abc-123"


def test_route_for_prompt_id_accepts_prefixed_id_leniently():
    # Prefix parsing is Story 6.3; this PR just proves the seam accepts the input.
    backend, _native = router.route_for_prompt_id("local:abc-123")
    assert isinstance(backend, LocalBackend)


def test_get_backend_local_returns_same_instance():
    # AC #7 — module-level singleton registry.
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

    assert result == {"status": "success", "prompt_id": "xyz"}
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
