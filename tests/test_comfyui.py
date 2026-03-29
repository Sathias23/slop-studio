import importlib
import json
import os

import httpx
import pytest
import respx

import comfyclaude.config
import comfyclaude.comfyui

SAMPLE_WORKFLOW = {
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 0,
            "steps": 4,
            "cfg": 1.0,
            "model": ["4", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "sampler_name": "euler",
            "scheduler": "simple",
            "latent_image": ["5", 0],
            "denoise": 1.0,
        },
    },
    "5": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "", "clip": ["4", 1]},
    },
}

SAMPLE_META = {
    "name": "test_template",
    "model": "test-model",
    "description": "Test template",
    "expected_duration": "5 seconds",
    "inputs": {
        "prompt": {
            "node_id": "6",
            "field": "text",
            "type": "required",
            "description": "Prompt text",
        }
    },
    "aspect_ratios": {
        "1:1": {"width": 1024, "height": 1024},
        "16:9": {"width": 1344, "height": 768},
    },
    "resolution_nodes": [
        {"node_id": "5", "width_field": "width", "height_field": "height"}
    ],
}


def write_template(templates_dir, name, workflow, meta):
    """Helper to write template files to the test directory."""
    (templates_dir / f"{name}.json").write_text(
        json.dumps(workflow), encoding="utf-8"
    )
    (templates_dir / f"{name}.meta.json").write_text(
        json.dumps(meta), encoding="utf-8"
    )


@pytest.fixture
def templates_dir(tmp_path, monkeypatch):
    """Set up a temporary templates directory with test fixtures."""
    monkeypatch.setenv("COMFYCLAUDE_TEMPLATES_DIR", str(tmp_path))
    monkeypatch.setenv("COMFYUI_URL", "http://test-comfyui:8188")
    importlib.reload(comfyclaude.config)
    importlib.reload(comfyclaude.comfyui)
    return tmp_path


@pytest.fixture
def sample_templates(templates_dir):
    """Write sample template files and return the templates dir."""
    write_template(templates_dir, "test_template", SAMPLE_WORKFLOW, SAMPLE_META)
    return templates_dir


COMFYUI_URL = "http://test-comfyui:8188"


@pytest.mark.anyio
@respx.mock
async def test_queue_prompt_success(sample_templates):
    respx.post(f"{COMFYUI_URL}/prompt").mock(
        return_value=httpx.Response(200, json={"prompt_id": "abc-123", "number": 1, "node_errors": {}})
    )
    result = await comfyclaude.comfyui.queue_prompt("test_template", {"prompt": "hello"})
    assert result == {"status": "success", "prompt_id": "abc-123"}


@pytest.mark.anyio
@respx.mock
async def test_input_injection(sample_templates):
    respx.post(f"{COMFYUI_URL}/prompt").mock(
        return_value=httpx.Response(200, json={"prompt_id": "abc-123", "number": 1, "node_errors": {}})
    )
    await comfyclaude.comfyui.queue_prompt("test_template", {"prompt": "hello"})

    request_body = json.loads(respx.calls.last.request.content)
    assert request_body["prompt"]["6"]["inputs"]["text"] == "hello"


@pytest.mark.anyio
@respx.mock
async def test_seed_randomization(sample_templates, monkeypatch):
    respx.post(f"{COMFYUI_URL}/prompt").mock(
        return_value=httpx.Response(200, json={"prompt_id": "abc-123", "number": 1, "node_errors": {}})
    )
    # Mock random.randint to return a known value
    monkeypatch.setattr(comfyclaude.comfyui.random, "randint", lambda a, b: 42)
    await comfyclaude.comfyui.queue_prompt("test_template", {"prompt": "hello"})

    request_body = json.loads(respx.calls.last.request.content)
    assert request_body["prompt"]["3"]["inputs"]["seed"] == 42


@pytest.mark.anyio
@respx.mock
async def test_aspect_ratio_injection(sample_templates):
    respx.post(f"{COMFYUI_URL}/prompt").mock(
        return_value=httpx.Response(200, json={"prompt_id": "abc-123", "number": 1, "node_errors": {}})
    )
    await comfyclaude.comfyui.queue_prompt(
        "test_template", {"prompt": "hello"}, aspect_ratio="16:9"
    )

    request_body = json.loads(respx.calls.last.request.content)
    assert request_body["prompt"]["5"]["inputs"]["width"] == 1344
    assert request_body["prompt"]["5"]["inputs"]["height"] == 768


@pytest.mark.anyio
@respx.mock
async def test_default_resolution_when_no_aspect_ratio(sample_templates):
    respx.post(f"{COMFYUI_URL}/prompt").mock(
        return_value=httpx.Response(200, json={"prompt_id": "abc-123", "number": 1, "node_errors": {}})
    )
    await comfyclaude.comfyui.queue_prompt("test_template", {"prompt": "hello"})

    request_body = json.loads(respx.calls.last.request.content)
    assert request_body["prompt"]["5"]["inputs"]["width"] == 1024
    assert request_body["prompt"]["5"]["inputs"]["height"] == 1024


@pytest.mark.anyio
@respx.mock
async def test_unreachable_comfyui_returns_transient_error(sample_templates):
    respx.post(f"{COMFYUI_URL}/prompt").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )
    result = await comfyclaude.comfyui.queue_prompt("test_template", {"prompt": "hello"})
    assert result["status"] == "error"
    assert result["error_type"] == "unreachable"
    assert result["retry_suggested"] is True


@pytest.mark.anyio
async def test_missing_template_returns_terminal_error(templates_dir):
    result = await comfyclaude.comfyui.queue_prompt("nonexistent", {"prompt": "hello"})
    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert result["retry_suggested"] is False
    assert "not found" in result["error"]


@pytest.mark.anyio
async def test_invalid_aspect_ratio_returns_terminal_error(sample_templates):
    result = await comfyclaude.comfyui.queue_prompt(
        "test_template", {"prompt": "hello"}, aspect_ratio="21:9"
    )
    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert result["retry_suggested"] is False
    assert "21:9" in result["error"]


@pytest.mark.anyio
async def test_missing_required_input_returns_terminal_error(sample_templates):
    result = await comfyclaude.comfyui.queue_prompt("test_template", {})
    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert result["retry_suggested"] is False
    assert "prompt" in result["error"]


@pytest.mark.anyio
@respx.mock
async def test_comfyui_400_returns_terminal_error(sample_templates):
    respx.post(f"{COMFYUI_URL}/prompt").mock(
        return_value=httpx.Response(400, text="Invalid workflow")
    )
    result = await comfyclaude.comfyui.queue_prompt("test_template", {"prompt": "hello"})
    assert result["status"] == "error"
    assert result["error_type"] == "invalid_workflow"
    assert result["retry_suggested"] is False


@pytest.mark.anyio
@respx.mock
async def test_comfyui_503_returns_transient_error(sample_templates):
    respx.post(f"{COMFYUI_URL}/prompt").mock(
        return_value=httpx.Response(503, text="Service Unavailable")
    )
    result = await comfyclaude.comfyui.queue_prompt("test_template", {"prompt": "hello"})
    assert result["status"] == "error"
    assert result["error_type"] == "unreachable"
    assert result["retry_suggested"] is True


# -- check_job test fixtures --

HISTORY_COMPLETED = {
    "abc-123": {
        "outputs": {
            "9": {"images": [{"filename": "ComfyUI_00042_.png", "subfolder": "", "type": "output"}]}
        },
        "status": {"status_str": "success", "completed": True, "messages": []},
    }
}

HISTORY_FAILED = {
    "abc-123": {
        "outputs": {},
        "status": {
            "status_str": "error",
            "completed": True,
            "messages": [
                ["execution_error", {"message": "Node type 'KSamplerAdvanced_v2' not found", "node_id": "3"}]
            ],
        },
    }
}

HISTORY_RUNNING = {
    "abc-123": {
        "outputs": {},
        "status": {"status_str": "", "completed": False, "messages": []},
    }
}

HISTORY_PENDING = {}


@pytest.mark.anyio
@respx.mock
async def test_check_job_single_check_completed(templates_dir):
    respx.get(f"{COMFYUI_URL}/history/abc-123").mock(
        return_value=httpx.Response(200, json=HISTORY_COMPLETED)
    )
    result = await comfyclaude.comfyui.check_job("abc-123")
    assert result["status"] == "completed"
    assert result["prompt_id"] == "abc-123"
    assert "9" in result["outputs"]


@pytest.mark.anyio
@respx.mock
async def test_check_job_single_check_pending(templates_dir):
    respx.get(f"{COMFYUI_URL}/history/abc-123").mock(
        return_value=httpx.Response(200, json=HISTORY_PENDING)
    )
    result = await comfyclaude.comfyui.check_job("abc-123")
    assert result == {"status": "pending", "prompt_id": "abc-123"}


@pytest.mark.anyio
@respx.mock
async def test_check_job_single_check_running(templates_dir):
    respx.get(f"{COMFYUI_URL}/history/abc-123").mock(
        return_value=httpx.Response(200, json=HISTORY_RUNNING)
    )
    result = await comfyclaude.comfyui.check_job("abc-123")
    assert result == {"status": "running", "prompt_id": "abc-123"}


@pytest.mark.anyio
@respx.mock
async def test_check_job_polling_returns_completed(templates_dir, monkeypatch):
    sleep_calls = []

    async def mock_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(comfyclaude.comfyui.asyncio, "sleep", mock_sleep)

    route = respx.get(f"{COMFYUI_URL}/history/abc-123")
    route.side_effect = [
        httpx.Response(200, json=HISTORY_RUNNING),
        httpx.Response(200, json=HISTORY_COMPLETED),
    ]
    result = await comfyclaude.comfyui.check_job("abc-123", wait=30)
    assert result["status"] == "completed"
    assert result["prompt_id"] == "abc-123"
    assert len(sleep_calls) == 1


@pytest.mark.anyio
@respx.mock
async def test_check_job_early_return_on_completion(templates_dir, monkeypatch):
    sleep_calls = []

    async def mock_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(comfyclaude.comfyui.asyncio, "sleep", mock_sleep)

    route = respx.get(f"{COMFYUI_URL}/history/abc-123")
    route.side_effect = [
        httpx.Response(200, json=HISTORY_RUNNING),
        httpx.Response(200, json=HISTORY_COMPLETED),
    ]
    result = await comfyclaude.comfyui.check_job("abc-123", wait=30)
    assert result["status"] == "completed"
    # Only one sleep call — returned early after first poll
    assert len(sleep_calls) == 1
    assert sleep_calls[0] == 3


@pytest.mark.anyio
@respx.mock
async def test_check_job_poll_timeout_returns_running(templates_dir, monkeypatch):
    async def mock_sleep(seconds):
        pass

    monkeypatch.setattr(comfyclaude.comfyui.asyncio, "sleep", mock_sleep)

    respx.get(f"{COMFYUI_URL}/history/abc-123").mock(
        return_value=httpx.Response(200, json=HISTORY_RUNNING)
    )
    result = await comfyclaude.comfyui.check_job("abc-123", wait=10)
    assert result == {"status": "running", "prompt_id": "abc-123"}


@pytest.mark.anyio
@respx.mock
async def test_check_job_failed_returns_error(templates_dir):
    respx.get(f"{COMFYUI_URL}/history/abc-123").mock(
        return_value=httpx.Response(200, json=HISTORY_FAILED)
    )
    result = await comfyclaude.comfyui.check_job("abc-123")
    assert result["status"] == "error"
    assert result["error_type"] == "generation_failed"
    assert result["retry_suggested"] is False


@pytest.mark.anyio
@respx.mock
async def test_check_job_unreachable_returns_transient_error(templates_dir):
    respx.get(f"{COMFYUI_URL}/history/abc-123").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )
    result = await comfyclaude.comfyui.check_job("abc-123")
    assert result["status"] == "error"
    assert result["error_type"] == "unreachable"
    assert result["retry_suggested"] is True


@pytest.mark.anyio
@respx.mock
async def test_check_job_poll_interval_is_3_seconds(templates_dir, monkeypatch):
    sleep_calls = []

    async def mock_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(comfyclaude.comfyui.asyncio, "sleep", mock_sleep)

    respx.get(f"{COMFYUI_URL}/history/abc-123").mock(
        return_value=httpx.Response(200, json=HISTORY_RUNNING)
    )
    await comfyclaude.comfyui.check_job("abc-123", wait=10)
    assert all(s == 3 for s in sleep_calls)
    assert len(sleep_calls) > 0


@pytest.mark.anyio
@respx.mock
async def test_check_job_wait_capped_at_45(templates_dir, monkeypatch):
    sleep_calls = []

    async def mock_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(comfyclaude.comfyui.asyncio, "sleep", mock_sleep)

    respx.get(f"{COMFYUI_URL}/history/abc-123").mock(
        return_value=httpx.Response(200, json=HISTORY_RUNNING)
    )
    await comfyclaude.comfyui.check_job("abc-123", wait=120)
    total_elapsed = sum(sleep_calls)
    assert total_elapsed <= 45
    # 45s / 3s = 15 max iterations
    assert len(sleep_calls) <= 15


@pytest.mark.anyio
async def test_mcp_check_job_registered():
    import comfyclaude.config as _config
    import comfyclaude.comfyui as _comfyui
    import comfyclaude.server as _server

    importlib.reload(_config)
    importlib.reload(_comfyui)
    importlib.reload(_server)

    tools = await _server.mcp.list_tools()
    tool_names = [t.name for t in tools]
    assert "check_job" in tool_names


@pytest.mark.anyio
async def test_mcp_queue_prompt_registered():
    import comfyclaude.config as _config
    import comfyclaude.comfyui as _comfyui
    import comfyclaude.server as _server

    importlib.reload(_config)
    importlib.reload(_comfyui)
    importlib.reload(_server)

    tools = await _server.mcp.list_tools()
    tool_names = [t.name for t in tools]
    assert "queue_prompt" in tool_names


# -- get_image test fixtures --

HISTORY_COMPLETED_WITH_IMAGE = {
    "abc-123": {
        "outputs": {
            "9": {"images": [{"filename": "ComfyUI_00042_.png", "subfolder": "", "type": "output"}]}
        },
        "status": {"status_str": "success", "completed": True, "messages": []},
    }
}

HISTORY_COMPLETED_NO_OUTPUT = {
    "abc-123": {
        "outputs": {},
        "status": {"status_str": "success", "completed": True, "messages": []},
    }
}

FAKE_IMAGE_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


@pytest.fixture
def output_dir(tmp_path, monkeypatch):
    """Override OUTPUT_DIR to use tmp_path for test isolation."""
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(comfyclaude.comfyui, "OUTPUT_DIR", str(out))
    return out


@pytest.mark.anyio
@respx.mock
async def test_get_image_completed_returns_file_path(templates_dir, output_dir):
    respx.get(f"{COMFYUI_URL}/history/abc-123").mock(
        return_value=httpx.Response(200, json=HISTORY_COMPLETED_WITH_IMAGE)
    )
    respx.get(f"{COMFYUI_URL}/view").mock(
        return_value=httpx.Response(200, content=FAKE_IMAGE_BYTES)
    )
    result = await comfyclaude.comfyui.get_image("abc-123")
    assert result["status"] == "success"
    assert result["prompt_id"] == "abc-123"
    assert os.path.isabs(result["file_path"])
    assert os.path.exists(result["file_path"])


@pytest.mark.anyio
@respx.mock
async def test_get_image_saves_in_date_directory(templates_dir, output_dir):
    from datetime import date

    respx.get(f"{COMFYUI_URL}/history/abc-123").mock(
        return_value=httpx.Response(200, json=HISTORY_COMPLETED_WITH_IMAGE)
    )
    respx.get(f"{COMFYUI_URL}/view").mock(
        return_value=httpx.Response(200, content=FAKE_IMAGE_BYTES)
    )
    result = await comfyclaude.comfyui.get_image("abc-123")
    today = date.today().isoformat()
    expected_path = output_dir / today / "ComfyUI_00042_.png"
    assert result["file_path"] == str(expected_path)


@pytest.mark.anyio
@respx.mock
async def test_get_image_creates_date_directory(templates_dir, output_dir):
    from datetime import date

    today = date.today().isoformat()
    date_dir = output_dir / today
    assert not date_dir.exists()

    respx.get(f"{COMFYUI_URL}/history/abc-123").mock(
        return_value=httpx.Response(200, json=HISTORY_COMPLETED_WITH_IMAGE)
    )
    respx.get(f"{COMFYUI_URL}/view").mock(
        return_value=httpx.Response(200, content=FAKE_IMAGE_BYTES)
    )
    await comfyclaude.comfyui.get_image("abc-123")
    assert date_dir.exists()


@pytest.mark.anyio
@respx.mock
async def test_get_image_sanitizes_filename(templates_dir, output_dir):
    from datetime import date

    history_with_traversal = {
        "abc-123": {
            "outputs": {
                "9": {"images": [{"filename": "../../etc/passwd", "subfolder": "", "type": "output"}]}
            },
            "status": {"status_str": "success", "completed": True, "messages": []},
        }
    }
    respx.get(f"{COMFYUI_URL}/history/abc-123").mock(
        return_value=httpx.Response(200, json=history_with_traversal)
    )
    respx.get(f"{COMFYUI_URL}/view").mock(
        return_value=httpx.Response(200, content=FAKE_IMAGE_BYTES)
    )
    result = await comfyclaude.comfyui.get_image("abc-123")
    today = date.today().isoformat()
    expected_path = output_dir / today / "passwd"
    assert result["file_path"] == str(expected_path)
    assert os.path.exists(result["file_path"])


@pytest.mark.anyio
@respx.mock
async def test_get_image_pending_returns_error(templates_dir, output_dir):
    respx.get(f"{COMFYUI_URL}/history/abc-123").mock(
        return_value=httpx.Response(200, json=HISTORY_PENDING)
    )
    result = await comfyclaude.comfyui.get_image("abc-123")
    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert result["retry_suggested"] is False


@pytest.mark.anyio
@respx.mock
async def test_get_image_running_returns_error(templates_dir, output_dir):
    respx.get(f"{COMFYUI_URL}/history/abc-123").mock(
        return_value=httpx.Response(200, json=HISTORY_RUNNING)
    )
    result = await comfyclaude.comfyui.get_image("abc-123")
    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert result["retry_suggested"] is False


@pytest.mark.anyio
@respx.mock
async def test_get_image_completed_no_output_returns_error(templates_dir, output_dir):
    respx.get(f"{COMFYUI_URL}/history/abc-123").mock(
        return_value=httpx.Response(200, json=HISTORY_COMPLETED_NO_OUTPUT)
    )
    result = await comfyclaude.comfyui.get_image("abc-123")
    assert result["status"] == "error"
    assert result["error_type"] == "completed_no_output"
    assert result["retry_suggested"] is False


@pytest.mark.anyio
@respx.mock
async def test_get_image_storage_error(templates_dir, output_dir, monkeypatch):
    respx.get(f"{COMFYUI_URL}/history/abc-123").mock(
        return_value=httpx.Response(200, json=HISTORY_COMPLETED_WITH_IMAGE)
    )
    respx.get(f"{COMFYUI_URL}/view").mock(
        return_value=httpx.Response(200, content=FAKE_IMAGE_BYTES)
    )
    monkeypatch.setattr(os, "makedirs", lambda *a, **kw: (_ for _ in ()).throw(OSError("Permission denied")))
    result = await comfyclaude.comfyui.get_image("abc-123")
    assert result["status"] == "error"
    assert result["error_type"] == "storage_error"
    assert result["retry_suggested"] is True


@pytest.mark.anyio
@respx.mock
async def test_get_image_unreachable_returns_transient_error(templates_dir, output_dir):
    respx.get(f"{COMFYUI_URL}/history/abc-123").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )
    result = await comfyclaude.comfyui.get_image("abc-123")
    assert result["status"] == "error"
    assert result["error_type"] == "unreachable"
    assert result["retry_suggested"] is True


@pytest.mark.anyio
async def test_mcp_get_image_registered():
    import comfyclaude.config as _config
    import comfyclaude.comfyui as _comfyui
    import comfyclaude.server as _server

    importlib.reload(_config)
    importlib.reload(_comfyui)
    importlib.reload(_server)

    tools = await _server.mcp.list_tools()
    tool_names = [t.name for t in tools]
    assert "get_image" in tool_names


@pytest.mark.anyio
@respx.mock
async def test_get_image_view_http_error_returns_transient_error(templates_dir, output_dir):
    respx.get(f"{COMFYUI_URL}/history/abc-123").mock(
        return_value=httpx.Response(200, json=HISTORY_COMPLETED_WITH_IMAGE)
    )
    respx.get(f"{COMFYUI_URL}/view").mock(
        return_value=httpx.Response(404, text="Not Found")
    )
    result = await comfyclaude.comfyui.get_image("abc-123")
    assert result["status"] == "error"
    assert result["error_type"] == "unreachable"
    assert result["retry_suggested"] is True


@pytest.mark.anyio
@respx.mock
async def test_fetch_job_status_non_json_returns_failed(templates_dir):
    respx.get(f"{COMFYUI_URL}/history/abc-123").mock(
        return_value=httpx.Response(200, text="not json")
    )
    result = await comfyclaude.comfyui.check_job("abc-123")
    assert result["status"] == "error"
    assert result["error_type"] == "generation_failed"


@pytest.mark.anyio
@respx.mock
async def test_get_image_dot_filename_returns_error(templates_dir, output_dir):
    history_dot = {
        "abc-123": {
            "outputs": {
                "9": {"images": [{"filename": ".", "subfolder": "", "type": "output"}]}
            },
            "status": {"status_str": "success", "completed": True, "messages": []},
        }
    }
    respx.get(f"{COMFYUI_URL}/history/abc-123").mock(
        return_value=httpx.Response(200, json=history_dot)
    )
    result = await comfyclaude.comfyui.get_image("abc-123")
    assert result["status"] == "error"
    assert result["error_type"] == "completed_no_output"


@pytest.mark.anyio
@respx.mock
async def test_get_image_passes_subfolder_to_view(templates_dir, output_dir):
    history_with_subfolder = {
        "abc-123": {
            "outputs": {
                "9": {"images": [{"filename": "ComfyUI_00042_.png", "subfolder": "subfolder_name", "type": "output"}]}
            },
            "status": {"status_str": "success", "completed": True, "messages": []},
        }
    }
    respx.get(f"{COMFYUI_URL}/history/abc-123").mock(
        return_value=httpx.Response(200, json=history_with_subfolder)
    )
    view_route = respx.get(f"{COMFYUI_URL}/view").mock(
        return_value=httpx.Response(200, content=FAKE_IMAGE_BYTES)
    )
    await comfyclaude.comfyui.get_image("abc-123")
    request = view_route.calls.last.request
    assert "subfolder=subfolder_name" in str(request.url)
