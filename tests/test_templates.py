import importlib
import json

import pytest

import comfyclaude.config
import comfyclaude.templates


@pytest.fixture
def templates_dir(tmp_path, monkeypatch):
    """Set TEMPLATES_DIR to a temporary directory and reload modules."""
    monkeypatch.setenv("COMFYCLAUDE_TEMPLATES_DIR", str(tmp_path))
    importlib.reload(comfyclaude.config)
    importlib.reload(comfyclaude.templates)
    return tmp_path


def _write_meta(directory, name, **overrides):
    """Helper to write a .meta.json file."""
    meta = {
        "name": name,
        "model": "TestModel",
        "description": f"Test template {name}",
        "expected_duration": "5 seconds",
        "inputs": {
            "prompt": {
                "node_id": "6",
                "field": "text",
                "type": "required",
                "description": "Test prompt",
            }
        },
        "aspect_ratios": {
            "1:1": {"width": 1024, "height": 1024},
            "16:9": {"width": 1344, "height": 768},
        },
        "resolution_nodes": [
            {"node_id": "5", "width_field": "width", "height_field": "height"}
        ],
        **overrides,
    }
    (directory / f"{name}.meta.json").write_text(json.dumps(meta))
    return meta


@pytest.mark.anyio
async def test_list_templates_returns_all(templates_dir):
    _write_meta(templates_dir, "alpha")
    _write_meta(templates_dir, "beta", model="OtherModel")

    result = await comfyclaude.templates.list_templates()

    assert result["status"] == "success"
    assert len(result["templates"]) == 2
    names = [t["name"] for t in result["templates"]]
    assert "alpha" in names
    assert "beta" in names
    for t in result["templates"]:
        assert set(t.keys()) == {"name", "model", "description", "aspect_ratios", "expected_duration"}
        assert isinstance(t["aspect_ratios"], list)


@pytest.mark.anyio
async def test_list_templates_empty_directory(templates_dir):
    result = await comfyclaude.templates.list_templates()

    assert result == {"status": "success", "templates": []}


@pytest.mark.anyio
async def test_list_templates_missing_directory(tmp_path, monkeypatch):
    nonexistent = str(tmp_path / "does_not_exist")
    monkeypatch.setenv("COMFYCLAUDE_TEMPLATES_DIR", nonexistent)
    importlib.reload(comfyclaude.config)
    importlib.reload(comfyclaude.templates)

    result = await comfyclaude.templates.list_templates()

    assert result == {"status": "success", "templates": []}


@pytest.mark.anyio
async def test_list_templates_skips_invalid_meta(templates_dir):
    _write_meta(templates_dir, "valid")
    (templates_dir / "broken.meta.json").write_text("{invalid json")

    result = await comfyclaude.templates.list_templates()

    assert result["status"] == "success"
    assert len(result["templates"]) == 1
    assert result["templates"][0]["name"] == "valid"


@pytest.mark.anyio
async def test_list_templates_skips_missing_required_key(templates_dir):
    _write_meta(templates_dir, "valid")
    (templates_dir / "no_name.meta.json").write_text(json.dumps({"model": "X", "description": "Y"}))

    result = await comfyclaude.templates.list_templates()

    assert result["status"] == "success"
    assert len(result["templates"]) == 1
    assert result["templates"][0]["name"] == "valid"


@pytest.mark.anyio
async def test_get_template_returns_full_metadata(templates_dir):
    meta = _write_meta(templates_dir, "mytemplate")

    result = await comfyclaude.templates.get_template("mytemplate")

    assert result["status"] == "success"
    assert result["name"] == "mytemplate"
    assert result["model"] == meta["model"]
    assert result["inputs"] == meta["inputs"]
    assert result["aspect_ratios"] == meta["aspect_ratios"]
    assert result["resolution_nodes"] == meta["resolution_nodes"]


@pytest.mark.anyio
async def test_get_template_not_found(templates_dir):
    result = await comfyclaude.templates.get_template("nonexistent")

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "nonexistent" in result["error"]
    assert result["retry_suggested"] is False


@pytest.mark.anyio
async def test_mcp_tools_registered():
    import comfyclaude.server

    importlib.reload(comfyclaude.config)
    importlib.reload(comfyclaude.templates)
    importlib.reload(comfyclaude.server)
    from comfyclaude.server import mcp

    tools = await mcp.list_tools()
    tool_names = [t.name for t in tools]
    assert "list_templates" in tool_names
    assert "get_template" in tool_names
