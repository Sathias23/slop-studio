"""Tests for slop_studio.models — check_requirements + download_models."""

import hashlib
import importlib
import json
from pathlib import Path

import httpx
import pytest
import respx

import slop_studio.config
import slop_studio.models
import slop_studio.templates

# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


def _reload_modules():
    importlib.reload(slop_studio.config)
    importlib.reload(slop_studio.templates)
    importlib.reload(slop_studio.models)


@pytest.fixture
def models_env(tmp_path, monkeypatch):
    """Set up an isolated templates dir and ComfyUI models dir under tmp_path.

    Yields ``(templates_dir, models_dir)``. Modules are reloaded after env
    var setup so the new dirs are picked up.
    """
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    models_dir = tmp_path / "comfyui" / "models"
    models_dir.mkdir(parents=True)

    monkeypatch.setenv("SLOP_STUDIO_TEMPLATES_DIR", str(templates_dir))
    monkeypatch.setenv("SLOP_STUDIO_COMFYUI_MODELS_DIR", str(models_dir))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("CIVITAI_API_KEY", raising=False)
    # Also redirect Path.home so credentials.json lookups don't hit the real
    # user's file.
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    _reload_modules()
    return templates_dir, models_dir


def _write_template(templates_dir, name, requirements=None, **extra_meta):
    """Write a minimal template + meta with the given model_requirements."""
    workflow_path = templates_dir / f"{name}.json"
    meta_path = templates_dir / f"{name}.meta.json"
    workflow_path.write_text(json.dumps({"6": {"inputs": {"text": ""}}}))
    meta = {
        "name": name,
        "model": "TestModel",
        "description": f"Template {name}",
    }
    if requirements is not None:
        meta["model_requirements"] = requirements
    meta.update(extra_meta)
    meta_path.write_text(json.dumps(meta))
    return meta


# ---------------------------------------------------------------------------
# check_requirements
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_check_requirements_no_declared(models_env):
    templates_dir, _ = models_env
    _write_template(templates_dir, "no_reqs")

    result = await slop_studio.models.check_requirements("no_reqs")

    assert result["status"] == "success"
    assert result["present"] == []
    assert result["missing"] == []
    assert "note" in result and "no local model requirements" in result["note"]


@pytest.mark.anyio
async def test_check_requirements_empty_list_treated_as_no_declared(models_env):
    templates_dir, _ = models_env
    _write_template(templates_dir, "empty_reqs", requirements=[])

    result = await slop_studio.models.check_requirements("empty_reqs")

    assert result["status"] == "success"
    assert result["present"] == []
    assert result["missing"] == []
    assert "note" in result


@pytest.mark.anyio
async def test_check_requirements_all_present(models_env):
    templates_dir, models_dir = models_env
    (models_dir / "unet").mkdir()
    (models_dir / "unet" / "model.gguf").write_bytes(b"weights")

    reqs = [
        {
            "filename": "model.gguf",
            "subfolder": "unet",
            "url": "https://example.com/model.gguf",
        }
    ]
    _write_template(templates_dir, "all_present", requirements=reqs)

    result = await slop_studio.models.check_requirements("all_present")

    assert result["status"] == "success"
    assert len(result["present"]) == 1
    assert result["present"][0]["filename"] == "model.gguf"
    assert result["missing"] == []


@pytest.mark.anyio
async def test_check_requirements_some_missing(models_env):
    templates_dir, models_dir = models_env
    (models_dir / "unet").mkdir()
    (models_dir / "unet" / "have.gguf").write_bytes(b"x")

    reqs = [
        {"filename": "have.gguf", "subfolder": "unet", "url": "https://example.com/have.gguf"},
        {"filename": "missing.gguf", "subfolder": "unet", "url": "https://example.com/missing.gguf"},
    ]
    _write_template(templates_dir, "mixed", requirements=reqs)

    result = await slop_studio.models.check_requirements("mixed")

    assert result["status"] == "success"
    present_names = [e["filename"] for e in result["present"]]
    missing_names = [e["filename"] for e in result["missing"]]
    assert present_names == ["have.gguf"]
    assert missing_names == ["missing.gguf"]


@pytest.mark.anyio
async def test_check_requirements_models_dir_missing(tmp_path, monkeypatch):
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    bogus_models = tmp_path / "does_not_exist"
    monkeypatch.setenv("SLOP_STUDIO_TEMPLATES_DIR", str(templates_dir))
    monkeypatch.setenv("SLOP_STUDIO_COMFYUI_MODELS_DIR", str(bogus_models))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    _reload_modules()

    _write_template(
        templates_dir,
        "needs_dir",
        requirements=[
            {"filename": "model.gguf", "subfolder": "unet", "url": "https://example.com/m.gguf"}
        ],
    )

    result = await slop_studio.models.check_requirements("needs_dir")

    assert result["status"] == "error"
    assert result["error_type"] == "directory_not_found"
    assert str(bogus_models) in result["error"]


@pytest.mark.anyio
async def test_check_requirements_template_not_found(models_env):
    result = await slop_studio.models.check_requirements("does_not_exist")

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"


# ---------------------------------------------------------------------------
# download_models — happy path & no-op
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_download_models_no_declared(models_env):
    templates_dir, _ = models_env
    _write_template(templates_dir, "no_reqs")

    result = await slop_studio.models.download_models("no_reqs")

    assert result["status"] == "success"
    assert result["downloaded"] == []
    assert "note" in result


@pytest.mark.anyio
async def test_download_models_all_already_present(models_env):
    templates_dir, models_dir = models_env
    (models_dir / "unet").mkdir()
    (models_dir / "unet" / "have.gguf").write_bytes(b"x")

    reqs = [
        {"filename": "have.gguf", "subfolder": "unet", "url": "https://example.com/have.gguf"}
    ]
    _write_template(templates_dir, "skip_all", requirements=reqs)

    result = await slop_studio.models.download_models("skip_all")

    assert result["status"] == "success"
    assert result["downloaded"] == []
    assert len(result["skipped"]) == 1


@pytest.mark.anyio
async def test_download_models_success_atomic_rename(models_env):
    templates_dir, models_dir = models_env
    payload = b"fake-model-bytes" * 100
    digest = hashlib.sha256(payload).hexdigest()

    reqs = [
        {
            "filename": "good.gguf",
            "subfolder": "unet",
            "url": "https://example.com/good.gguf",
            "sha256": digest,
            "size_bytes": len(payload),
        }
    ]
    _write_template(templates_dir, "ok_dl", requirements=reqs)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://example.com/good.gguf").mock(
            return_value=httpx.Response(200, content=payload)
        )
        result = await slop_studio.models.download_models("ok_dl")

    assert result["status"] == "success", result
    assert len(result["downloaded"]) == 1
    target = models_dir / "unet" / "good.gguf"
    assert target.is_file()
    assert target.read_bytes() == payload
    # No .partial left behind.
    assert not (models_dir / "unet" / "good.gguf.partial").exists()


@pytest.mark.anyio
async def test_download_models_creates_subfolder(models_env):
    templates_dir, models_dir = models_env
    payload = b"weights"
    reqs = [
        {
            "filename": "m.gguf",
            "subfolder": "newsubfolder",
            "url": "https://example.com/m.gguf",
        }
    ]
    _write_template(templates_dir, "newsub", requirements=reqs)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://example.com/m.gguf").mock(return_value=httpx.Response(200, content=payload))
        result = await slop_studio.models.download_models("newsub")

    assert result["status"] == "success"
    assert (models_dir / "newsubfolder" / "m.gguf").is_file()


# ---------------------------------------------------------------------------
# download_models — failure modes (atomic cleanup)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_download_models_sha256_mismatch_cleans_partial(models_env):
    templates_dir, models_dir = models_env
    payload = b"actual content"
    bogus_digest = "0" * 64

    reqs = [
        {
            "filename": "bad_sha.gguf",
            "subfolder": "unet",
            "url": "https://example.com/bad_sha.gguf",
            "sha256": bogus_digest,
        }
    ]
    _write_template(templates_dir, "bad_sha", requirements=reqs)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://example.com/bad_sha.gguf").mock(
            return_value=httpx.Response(200, content=payload)
        )
        result = await slop_studio.models.download_models("bad_sha")

    assert result["status"] == "error"
    assert result["error_type"] == "verification_failed"
    assert bogus_digest in result["error"]
    # Critical: no partial, no target.
    assert not (models_dir / "unet" / "bad_sha.gguf").exists()
    assert not (models_dir / "unet" / "bad_sha.gguf.partial").exists()


@pytest.mark.anyio
async def test_download_models_network_error_cleans_partial(models_env):
    templates_dir, models_dir = models_env

    reqs = [
        {
            "filename": "neterr.gguf",
            "subfolder": "unet",
            "url": "https://example.com/neterr.gguf",
        }
    ]
    _write_template(templates_dir, "neterr", requirements=reqs)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://example.com/neterr.gguf").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        result = await slop_studio.models.download_models("neterr")

    assert result["status"] == "error"
    assert result["error_type"] == "network_error"
    assert result["retry_suggested"] is True
    assert not (models_dir / "unet" / "neterr.gguf").exists()
    assert not (models_dir / "unet" / "neterr.gguf.partial").exists()


@pytest.mark.anyio
async def test_download_models_http_500_treated_as_network_error(models_env):
    templates_dir, models_dir = models_env

    reqs = [
        {
            "filename": "five.gguf",
            "subfolder": "unet",
            "url": "https://example.com/five.gguf",
        }
    ]
    _write_template(templates_dir, "five", requirements=reqs)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://example.com/five.gguf").mock(return_value=httpx.Response(500))
        result = await slop_studio.models.download_models("five")

    assert result["status"] == "error"
    assert result["error_type"] == "network_error"
    assert not (models_dir / "unet" / "five.gguf.partial").exists()


@pytest.mark.anyio
async def test_download_models_auth_required_no_token_returns_terminal_no_network(models_env):
    templates_dir, models_dir = models_env

    reqs = [
        {
            "filename": "private.gguf",
            "subfolder": "unet",
            "url": "https://huggingface.co/example/private.gguf",
            "auth": "huggingface",
        }
    ]
    _write_template(templates_dir, "needs_hf", requirements=reqs)

    with respx.mock(assert_all_called=False) as mock:
        # Register the route but expect it NOT to be called.
        route = mock.get("https://huggingface.co/example/private.gguf").mock(
            return_value=httpx.Response(200, content=b"x")
        )
        result = await slop_studio.models.download_models("needs_hf")

    assert result["status"] == "error"
    assert result["error_type"] == "auth_failed"
    assert "HF_TOKEN" in result["error"]
    assert route.call_count == 0
    assert not (models_dir / "unet" / "private.gguf.partial").exists()


@pytest.mark.anyio
async def test_download_models_civitai_auth_required_no_token(models_env):
    templates_dir, _ = models_env

    reqs = [
        {
            "filename": "civ.safetensors",
            "subfolder": "loras",
            "url": "https://civitai.com/api/download/123",
            "auth": "civitai",
        }
    ]
    _write_template(templates_dir, "needs_civ", requirements=reqs)

    result = await slop_studio.models.download_models("needs_civ")

    assert result["status"] == "error"
    assert result["error_type"] == "auth_failed"
    assert "CIVITAI_API_KEY" in result["error"]


@pytest.mark.anyio
async def test_download_models_auth_401_cleans_partial(models_env, monkeypatch):
    templates_dir, models_dir = models_env
    monkeypatch.setenv("HF_TOKEN", "fake-token")
    _reload_modules()

    reqs = [
        {
            "filename": "denied.gguf",
            "subfolder": "unet",
            "url": "https://huggingface.co/example/denied.gguf",
            "auth": "huggingface",
        }
    ]
    _write_template(templates_dir, "denied", requirements=reqs)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://huggingface.co/example/denied.gguf").mock(
            return_value=httpx.Response(401, content=b"forbidden")
        )
        result = await slop_studio.models.download_models("denied")

    assert result["status"] == "error"
    assert result["error_type"] == "auth_failed"
    assert not (models_dir / "unet" / "denied.gguf.partial").exists()


@pytest.mark.anyio
async def test_download_models_auth_token_sent_in_header(models_env, monkeypatch):
    templates_dir, _ = models_env
    monkeypatch.setenv("HF_TOKEN", "secret-hf-token")
    _reload_modules()

    payload = b"private bits"
    reqs = [
        {
            "filename": "auth_ok.gguf",
            "subfolder": "unet",
            "url": "https://huggingface.co/example/auth_ok.gguf",
            "auth": "huggingface",
        }
    ]
    _write_template(templates_dir, "auth_ok", requirements=reqs)

    captured_headers: dict = {}

    def _capture(request):
        captured_headers.update(dict(request.headers))
        return httpx.Response(200, content=payload)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://huggingface.co/example/auth_ok.gguf").mock(side_effect=_capture)
        result = await slop_studio.models.download_models("auth_ok")

    assert result["status"] == "success"
    assert captured_headers.get("authorization") == "Bearer secret-hf-token"


@pytest.mark.anyio
async def test_download_models_concurrent_collision_keeps_existing(models_env, monkeypatch):
    """If target appears between the existence check and rename, keep existing.

    Uses a Path.replace monkeypatch (the operation invoked AFTER the existence
    check) to plant the "existing" file, rather than globally patching
    Path.exists for every call site in the run.
    """
    templates_dir, models_dir = models_env

    target_dir = models_dir / "unet"
    target_dir.mkdir(exist_ok=True)
    payload = b"new-download"

    reqs = [
        {
            "filename": "race.gguf",
            "subfolder": "unet",
            "url": "https://example.com/race.gguf",
        }
    ]
    _write_template(templates_dir, "race", requirements=reqs)

    target_path = target_dir / "race.gguf"
    partial_path = target_dir / "race.gguf.partial"

    # Plant the "existing" file just before the post-stream existence check
    # by hooking the .partial file's Path.replace — at that point the stream
    # has finished but rename hasn't happened. We trigger via Path.is_file
    # being called on the partial during cleanup, but simpler: hook the
    # partial path's stat sequence by intercepting Path.replace itself.
    #
    # The post-stream code path in _download_one is:
    #   1. write to partial
    #   2. check target.exists() → if True: drop partial, return existing
    #   3. else partial.replace(target)
    # To force the (2) branch, we need target.exists() == True at step 2.
    # We do this by writing the existing target file the moment the partial
    # is written — i.e., intercept the partial open() via patching pathlib's
    # write to the partial.

    def _planted_replace(self, target_arg):
        # Should not be reached in this test path — but if it is, fail loud.
        raise AssertionError(
            f"unexpected Path.replace call: {self} -> {target_arg}; "
            "test expected the post-stream existence check to short-circuit"
        )

    # The existence check is done via target.exists(). We need that to be
    # True only when called from within _download_one's post-stream branch.
    # Rather than patching Path.exists globally, patch the bound method on
    # the specific target_path instance by writing it before the rename
    # check. We do that by hooking `open()` calls — when the partial is
    # being closed (write done), plant the target.
    real_open = open
    planted = {"done": False}

    def _planting_open(file, mode="r", *args, **kwargs):
        result = real_open(file, mode, *args, **kwargs)
        # When we open the .partial for writing, schedule a plant to fire
        # on close.
        if (
            isinstance(file, (str, Path))
            and str(file).endswith("race.gguf.partial")
            and "w" in mode
        ):
            real_close = result.close

            def _close_then_plant(*a, **kw):
                rc = real_close(*a, **kw)
                if not planted["done"]:
                    target_path.write_bytes(b"existing")
                    planted["done"] = True
                return rc

            result.close = _close_then_plant
        return result

    monkeypatch.setattr("builtins.open", _planting_open)
    monkeypatch.setattr(Path, "replace", _planted_replace)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://example.com/race.gguf").mock(
            return_value=httpx.Response(200, content=payload)
        )
        result = await slop_studio.models.download_models("race")

    assert result["status"] == "success", result
    # Existing file kept.
    assert target_path.read_bytes() == b"existing"
    # No .partial left.
    assert not partial_path.exists()


@pytest.mark.anyio
async def test_download_models_models_dir_missing(tmp_path, monkeypatch):
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    bogus = tmp_path / "no_such_dir"
    monkeypatch.setenv("SLOP_STUDIO_TEMPLATES_DIR", str(templates_dir))
    monkeypatch.setenv("SLOP_STUDIO_COMFYUI_MODELS_DIR", str(bogus))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    _reload_modules()

    _write_template(
        templates_dir,
        "needs_dir2",
        requirements=[
            {"filename": "m.gguf", "subfolder": "unet", "url": "https://example.com/m.gguf"}
        ],
    )

    result = await slop_studio.models.download_models("needs_dir2")

    assert result["status"] == "error"
    assert result["error_type"] == "directory_not_found"
    assert str(bogus) in result["error"]


@pytest.mark.anyio
async def test_download_models_partial_cleanup_after_first_failure_in_batch(models_env):
    """Two-entry batch: first succeeds, second fails sha256 -> halt with no partials."""
    templates_dir, models_dir = models_env

    payload_a = b"first model"
    digest_a = hashlib.sha256(payload_a).hexdigest()
    payload_b = b"second model"
    bogus_digest = "f" * 64

    reqs = [
        {
            "filename": "a.gguf",
            "subfolder": "unet",
            "url": "https://example.com/a.gguf",
            "sha256": digest_a,
        },
        {
            "filename": "b.gguf",
            "subfolder": "unet",
            "url": "https://example.com/b.gguf",
            "sha256": bogus_digest,
        },
    ]
    _write_template(templates_dir, "batch", requirements=reqs)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://example.com/a.gguf").mock(return_value=httpx.Response(200, content=payload_a))
        mock.get("https://example.com/b.gguf").mock(return_value=httpx.Response(200, content=payload_b))
        result = await slop_studio.models.download_models("batch")

    assert result["status"] == "error"
    assert result["error_type"] == "verification_failed"
    # First file successfully downloaded before the failure.
    assert (models_dir / "unet" / "a.gguf").is_file()
    # No partials anywhere.
    assert not (models_dir / "unet" / "a.gguf.partial").exists()
    assert not (models_dir / "unet" / "b.gguf").exists()
    assert not (models_dir / "unet" / "b.gguf.partial").exists()


# ---------------------------------------------------------------------------
# P1 — redirect handling (https→https only, cross-origin Authorization stripping)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_download_models_redirect_to_non_https_rejected(models_env):
    """A 302 to a http:// (non-https) target must return network_error and
    must NOT write a .partial."""
    templates_dir, models_dir = models_env

    reqs = [
        {
            "filename": "redirected.gguf",
            "subfolder": "unet",
            "url": "https://example.com/redirected.gguf",
        }
    ]
    _write_template(templates_dir, "non_https_redir", requirements=reqs)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://example.com/redirected.gguf").mock(
            return_value=httpx.Response(
                302, headers={"Location": "http://insecure.example.com/cdn/file"}
            )
        )
        result = await slop_studio.models.download_models("non_https_redir")

    assert result["status"] == "error"
    assert result["error_type"] == "network_error"
    assert "non-https" in result["error"]
    assert not (models_dir / "unet" / "redirected.gguf").exists()
    assert not (models_dir / "unet" / "redirected.gguf.partial").exists()


@pytest.mark.anyio
async def test_download_models_redirect_cross_origin_strips_authorization(
    models_env, monkeypatch
):
    """A redirect from huggingface.co to a different host must DROP the
    Authorization header on the second request to avoid leaking the bearer
    token to a CDN."""
    templates_dir, models_dir = models_env
    monkeypatch.setenv("HF_TOKEN", "secret-bearer")
    _reload_modules()

    payload = b"final-bytes"
    reqs = [
        {
            "filename": "hf_redir.gguf",
            "subfolder": "unet",
            "url": "https://huggingface.co/example/hf_redir.gguf",
            "auth": "huggingface",
        }
    ]
    _write_template(templates_dir, "hf_redir", requirements=reqs)

    captured: list[dict] = []

    def _capture_first(request):
        captured.append({"url": str(request.url), "headers": dict(request.headers)})
        return httpx.Response(
            302, headers={"Location": "https://cdn.example.com/blob/hf_redir.gguf"}
        )

    def _capture_second(request):
        captured.append({"url": str(request.url), "headers": dict(request.headers)})
        return httpx.Response(200, content=payload)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://huggingface.co/example/hf_redir.gguf").mock(side_effect=_capture_first)
        mock.get("https://cdn.example.com/blob/hf_redir.gguf").mock(side_effect=_capture_second)
        result = await slop_studio.models.download_models("hf_redir")

    assert result["status"] == "success", result
    assert (models_dir / "unet" / "hf_redir.gguf").read_bytes() == payload
    assert len(captured) == 2
    # First hop (origin) carries the bearer.
    assert captured[0]["headers"].get("authorization") == "Bearer secret-bearer"
    # Second hop (CDN) must NOT carry the bearer.
    assert "authorization" not in {k.lower() for k in captured[1]["headers"]}


@pytest.mark.anyio
async def test_download_models_too_many_redirects(models_env):
    """A redirect chain longer than the cap returns network_error."""
    templates_dir, models_dir = models_env

    reqs = [
        {
            "filename": "loopy.gguf",
            "subfolder": "unet",
            "url": "https://example.com/hop0",
        }
    ]
    _write_template(templates_dir, "loopy", requirements=reqs)

    with respx.mock(assert_all_called=False) as mock:
        # 7 hops > 5 cap.
        for i in range(7):
            mock.get(f"https://example.com/hop{i}").mock(
                return_value=httpx.Response(
                    302, headers={"Location": f"https://example.com/hop{i + 1}"}
                )
            )
        mock.get("https://example.com/hop7").mock(return_value=httpx.Response(200, content=b"x"))
        result = await slop_studio.models.download_models("loopy")

    assert result["status"] == "error"
    assert result["error_type"] == "network_error"
    assert "too many redirects" in result["error"].lower()
    assert not (models_dir / "unet" / "loopy.gguf").exists()
    assert not (models_dir / "unet" / "loopy.gguf.partial").exists()


@pytest.mark.anyio
async def test_download_models_protocol_relative_redirect_resolves(models_env):
    """A 302 with a protocol-relative `//host/path` Location must inherit the
    base scheme (https) and follow successfully — not be misclassified as
    non-https."""
    templates_dir, models_dir = models_env

    payload = b"protocol-relative-bytes"
    reqs = [
        {
            "filename": "rel.gguf",
            "subfolder": "unet",
            "url": "https://example.com/start",
        }
    ]
    _write_template(templates_dir, "rel", requirements=reqs)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://example.com/start").mock(
            return_value=httpx.Response(
                302, headers={"Location": "//cdn.example.com/blob/rel.gguf"}
            )
        )
        mock.get("https://cdn.example.com/blob/rel.gguf").mock(
            return_value=httpx.Response(200, content=payload)
        )
        result = await slop_studio.models.download_models("rel")

    assert result["status"] == "success", result
    assert (models_dir / "unet" / "rel.gguf").read_bytes() == payload


@pytest.mark.anyio
async def test_download_models_no_requirements_includes_skipped(models_env):
    """The no-requirements response from download_models must include both
    `downloaded` and `skipped` fields so callers can index either without
    a KeyError."""
    templates_dir, _ = models_env
    _write_template(templates_dir, "empty_reqs", requirements=[])

    result = await slop_studio.models.download_models("empty_reqs")

    assert result["status"] == "success"
    assert result["downloaded"] == []
    assert result["skipped"] == []
    assert "note" in result


@pytest.mark.anyio
async def test_download_models_single_https_redirect_succeeds(models_env):
    """A single https→https redirect (within hop budget) should succeed."""
    templates_dir, models_dir = models_env

    payload = b"redirected-content"
    reqs = [
        {
            "filename": "ok_redir.gguf",
            "subfolder": "unet",
            "url": "https://example.com/start",
        }
    ]
    _write_template(templates_dir, "ok_redir", requirements=reqs)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://example.com/start").mock(
            return_value=httpx.Response(
                302, headers={"Location": "https://cdn.example.com/file"}
            )
        )
        mock.get("https://cdn.example.com/file").mock(
            return_value=httpx.Response(200, content=payload)
        )
        result = await slop_studio.models.download_models("ok_redir")

    assert result["status"] == "success"
    assert (models_dir / "unet" / "ok_redir.gguf").read_bytes() == payload


# ---------------------------------------------------------------------------
# P2 — size_bytes verification + empty-body rejection
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_download_models_size_bytes_mismatch_returns_verification_failed(models_env):
    templates_dir, models_dir = models_env

    payload = b"fifty bytes" * 5  # 55 bytes; declared 100 — mismatch
    reqs = [
        {
            "filename": "size_mismatch.gguf",
            "subfolder": "unet",
            "url": "https://example.com/size_mismatch.gguf",
            "size_bytes": 100,
        }
    ]
    _write_template(templates_dir, "size_mismatch", requirements=reqs)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://example.com/size_mismatch.gguf").mock(
            return_value=httpx.Response(200, content=payload)
        )
        result = await slop_studio.models.download_models("size_mismatch")

    assert result["status"] == "error"
    assert result["error_type"] == "verification_failed"
    assert "size mismatch" in result["error"]
    assert not (models_dir / "unet" / "size_mismatch.gguf").exists()
    assert not (models_dir / "unet" / "size_mismatch.gguf.partial").exists()


@pytest.mark.anyio
async def test_download_models_size_bytes_match_succeeds(models_env):
    templates_dir, models_dir = models_env

    payload = b"twentybytestotaaal!!"  # 20 bytes
    assert len(payload) == 20
    reqs = [
        {
            "filename": "size_ok.gguf",
            "subfolder": "unet",
            "url": "https://example.com/size_ok.gguf",
            "size_bytes": 20,
        }
    ]
    _write_template(templates_dir, "size_ok", requirements=reqs)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://example.com/size_ok.gguf").mock(
            return_value=httpx.Response(200, content=payload)
        )
        result = await slop_studio.models.download_models("size_ok")

    assert result["status"] == "success"
    assert (models_dir / "unet" / "size_ok.gguf").read_bytes() == payload


@pytest.mark.anyio
async def test_download_models_empty_body_rejected(models_env):
    templates_dir, models_dir = models_env

    reqs = [
        {
            "filename": "empty.gguf",
            "subfolder": "unet",
            "url": "https://example.com/empty.gguf",
        }
    ]
    _write_template(templates_dir, "empty_body", requirements=reqs)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://example.com/empty.gguf").mock(
            return_value=httpx.Response(200, content=b"")
        )
        result = await slop_studio.models.download_models("empty_body")

    assert result["status"] == "error"
    assert result["error_type"] == "network_error"
    assert "empty body" in result["error"].lower()
    assert not (models_dir / "unet" / "empty.gguf").exists()
    assert not (models_dir / "unet" / "empty.gguf.partial").exists()


# ---------------------------------------------------------------------------
# P3 — mkdir failure returns storage_error cleanly
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_download_models_mkdir_failure_returns_storage_error(
    models_env, monkeypatch
):
    templates_dir, models_dir = models_env

    reqs = [
        {
            "filename": "no_dir.gguf",
            "subfolder": "newdir",
            "url": "https://example.com/no_dir.gguf",
        }
    ]
    _write_template(templates_dir, "no_dir", requirements=reqs)

    real_mkdir = Path.mkdir
    target_subfolder = (models_dir / "newdir").resolve()

    def _fake_mkdir(self, *args, **kwargs):
        if Path(self).resolve() == target_subfolder:
            raise PermissionError("read-only filesystem")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", _fake_mkdir)

    with respx.mock(assert_all_called=False) as mock:
        # Route registered so respx doesn't error if it gets called, but
        # we expect mkdir to fail before any network IO.
        mock.get("https://example.com/no_dir.gguf").mock(
            return_value=httpx.Response(200, content=b"x")
        )
        result = await slop_studio.models.download_models("no_dir")

    assert result["status"] == "error"
    assert result["error_type"] == "storage_error"


# ---------------------------------------------------------------------------
# P4 — LocalProtocolError → auth_failed
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_download_models_local_protocol_error_returns_auth_failed(
    models_env, monkeypatch
):
    """Simulate a LocalProtocolError raised at request build time (e.g. CR
    in the bearer token). The catch must classify it as auth_failed rather
    than letting it slip through the generic httpx.HTTPError branch."""
    templates_dir, models_dir = models_env

    reqs = [
        {
            "filename": "lpe.gguf",
            "subfolder": "unet",
            "url": "https://huggingface.co/example/lpe.gguf",
            "auth": "huggingface",
        }
    ]
    _write_template(templates_dir, "lpe", requirements=reqs)

    # Patch _get_token_for_auth to return a CR-tainted token. (The
    # _get_credential helper would normally reject this; we bypass to
    # exercise the LocalProtocolError catch branch directly.)
    monkeypatch.setattr(
        slop_studio.models, "_get_token_for_auth", lambda auth: "tainted\rvalue"
    )

    result = await slop_studio.models.download_models("lpe")

    assert result["status"] == "error"
    assert result["error_type"] == "auth_failed"
    assert "Malformed credential" in result["error"] or "auth" in result["error"].lower()
    assert not (models_dir / "unet" / "lpe.gguf.partial").exists()


# ---------------------------------------------------------------------------
# P7 — compound suffix preserved in .partial path
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_download_models_partial_path_preserves_compound_suffix(models_env):
    """For ``model.tar.gz`` the .partial path must be ``model.tar.gz.partial``,
    not ``model.tar.partial`` (which would happen with target.with_suffix)."""
    templates_dir, models_dir = models_env

    payload = b"tarball-body"
    reqs = [
        {
            "filename": "model.tar.gz",
            "subfolder": "unet",
            "url": "https://example.com/model.tar.gz",
        }
    ]
    _write_template(templates_dir, "compound", requirements=reqs)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://example.com/model.tar.gz").mock(
            return_value=httpx.Response(200, content=payload)
        )
        result = await slop_studio.models.download_models("compound")

    assert result["status"] == "success"
    target = models_dir / "unet" / "model.tar.gz"
    assert target.is_file()
    # Critical: ensure we never created a "model.tar.partial" file
    # (which would shadow the existing tarball if the run were aborted).
    assert not (models_dir / "unet" / "model.tar.partial").exists()
    assert not (models_dir / "unet" / "model.tar.gz.partial").exists()


# ---------------------------------------------------------------------------
# P8 — check_requirements is_file() OSError → storage_error
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_check_requirements_is_file_oserror_returns_storage_error(
    models_env, monkeypatch
):
    templates_dir, _ = models_env

    reqs = [
        {
            "filename": "stat_fail.gguf",
            "subfolder": "unet",
            "url": "https://example.com/stat_fail.gguf",
        }
    ]
    _write_template(templates_dir, "stat_fail", requirements=reqs)

    real_is_file = Path.is_file

    def _fake_is_file(self):
        if self.name == "stat_fail.gguf":
            raise PermissionError("denied")
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", _fake_is_file)

    result = await slop_studio.models.check_requirements("stat_fail")

    assert result["status"] == "error"
    assert result["error_type"] == "storage_error"
    assert "Cannot stat" in result["error"]
