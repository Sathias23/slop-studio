import importlib
import json

import pytest

import slop_studio.config
import slop_studio.templates


@pytest.fixture
def templates_dir(tmp_path, monkeypatch):
    """Set TEMPLATES_DIR to a temporary directory and reload modules."""
    monkeypatch.setenv("SLOP_STUDIO_TEMPLATES_DIR", str(tmp_path))
    importlib.reload(slop_studio.config)
    importlib.reload(slop_studio.templates)
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
        "resolution_nodes": [{"node_id": "5", "width_field": "width", "height_field": "height"}],
        **overrides,
    }
    (directory / f"{name}.meta.json").write_text(json.dumps(meta))
    return meta


@pytest.mark.anyio
async def test_list_templates_returns_all(templates_dir):
    _write_meta(templates_dir, "alpha")
    _write_meta(templates_dir, "beta", model="OtherModel")

    result = await slop_studio.templates.list_templates()

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
    result = await slop_studio.templates.list_templates()

    assert result == {"status": "success", "templates": []}


@pytest.mark.anyio
async def test_list_templates_missing_directory(tmp_path, monkeypatch):
    nonexistent = str(tmp_path / "does_not_exist")
    monkeypatch.setenv("SLOP_STUDIO_TEMPLATES_DIR", nonexistent)
    importlib.reload(slop_studio.config)
    importlib.reload(slop_studio.templates)

    result = await slop_studio.templates.list_templates()

    assert result == {"status": "success", "templates": []}


@pytest.mark.anyio
async def test_list_templates_skips_invalid_meta(templates_dir):
    _write_meta(templates_dir, "valid")
    (templates_dir / "broken.meta.json").write_text("{invalid json")

    result = await slop_studio.templates.list_templates()

    assert result["status"] == "success"
    assert len(result["templates"]) == 1
    assert result["templates"][0]["name"] == "valid"


@pytest.mark.anyio
async def test_list_templates_skips_missing_required_key(templates_dir):
    _write_meta(templates_dir, "valid")
    (templates_dir / "no_name.meta.json").write_text(json.dumps({"model": "X", "description": "Y"}))

    result = await slop_studio.templates.list_templates()

    assert result["status"] == "success"
    assert len(result["templates"]) == 1
    assert result["templates"][0]["name"] == "valid"


@pytest.mark.anyio
async def test_get_template_returns_full_metadata(templates_dir):
    meta = _write_meta(templates_dir, "mytemplate")

    result = await slop_studio.templates.get_template("mytemplate")

    assert result["status"] == "success"
    assert result["name"] == "mytemplate"
    assert result["model"] == meta["model"]
    assert result["inputs"] == meta["inputs"]
    assert result["aspect_ratios"] == meta["aspect_ratios"]
    assert result["resolution_nodes"] == meta["resolution_nodes"]


@pytest.mark.anyio
async def test_get_template_not_found(templates_dir):
    result = await slop_studio.templates.get_template("nonexistent")

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "nonexistent" in result["error"]
    assert result["retry_suggested"] is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    "bad_name",
    [
        "../evil",
        "foo/bar",
        ".hidden",
        "a..b",
        "",
        "   ",
    ],
)
async def test_get_template_rejects_path_traversal(templates_dir, bad_name):
    result = await slop_studio.templates.get_template(bad_name)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert result["retry_suggested"] is False


SAMPLE_WORKFLOW = {
    "6": {"inputs": {"text": ""}, "class_type": "CLIPTextEncode"},
    "47": {"inputs": {"width": 1024, "height": 1024}, "class_type": "EmptyLatentImage"},
}

SAMPLE_METADATA = {
    "name": "test_template",
    "model": "test-model",
    "description": "A test template",
    "inputs": {"prompt": {"node_id": "6", "field": "text", "type": "required", "description": "Text prompt"}},
    "aspect_ratios": {"1:1": {"width": 1024, "height": 1024}},
    "resolution_nodes": [{"node_id": "47", "width_field": "width", "height_field": "height"}],
    "expected_duration": "10 seconds",
}


def _sample_meta(**overrides):
    """Return a copy of SAMPLE_METADATA with overrides."""
    meta = {**SAMPLE_METADATA, **overrides}
    return meta


# ── add_template tests ──


@pytest.mark.anyio
async def test_add_template_creates_files(templates_dir):
    result = await slop_studio.templates.add_template("new_tpl", SAMPLE_WORKFLOW, _sample_meta())

    assert result["status"] == "success"
    assert result["name"] == "new_tpl"
    assert (templates_dir / "new_tpl.json").exists()
    assert (templates_dir / "new_tpl.meta.json").exists()
    workflow = json.loads((templates_dir / "new_tpl.json").read_text())
    assert workflow == SAMPLE_WORKFLOW
    meta = json.loads((templates_dir / "new_tpl.meta.json").read_text())
    assert meta["name"] == "new_tpl"
    assert meta["model"] == "test-model"


@pytest.mark.anyio
async def test_add_template_returns_success(templates_dir):
    result = await slop_studio.templates.add_template("foo", SAMPLE_WORKFLOW, _sample_meta())

    assert result == {"status": "success", "name": "foo", "message": "Template 'foo' added"}


@pytest.mark.anyio
@pytest.mark.parametrize("bad_name", ["../evil", "foo/bar", ".hidden", "a..b", "", "   "])
async def test_add_template_rejects_bad_names(templates_dir, bad_name):
    result = await slop_studio.templates.add_template(bad_name, SAMPLE_WORKFLOW, _sample_meta())

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert result["retry_suggested"] is False


@pytest.mark.anyio
async def test_add_template_rejects_empty_name(templates_dir):
    result = await slop_studio.templates.add_template("", SAMPLE_WORKFLOW, _sample_meta())

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "empty" in result["error"].lower()


@pytest.mark.anyio
async def test_add_template_rejects_existing(templates_dir):
    _write_meta(templates_dir, "existing")
    (templates_dir / "existing.json").write_text("{}")

    result = await slop_studio.templates.add_template("existing", SAMPLE_WORKFLOW, _sample_meta())

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "already exists" in result["error"]


@pytest.mark.anyio
async def test_add_template_rejects_missing_meta_fields(templates_dir):
    result = await slop_studio.templates.add_template("bad_meta", SAMPLE_WORKFLOW, {"description": "no model"})

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "model" in result["error"]


@pytest.mark.anyio
async def test_add_template_rejects_invalid_input_definition(templates_dir):
    meta = _sample_meta(inputs={"prompt": {"field": "text"}})  # missing node_id

    result = await slop_studio.templates.add_template("bad_input", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "node_id" in result["error"]


@pytest.mark.anyio
async def test_add_template_rejects_invalid_resolution_nodes(templates_dir):
    meta = _sample_meta(resolution_nodes=[{"node_id": "5"}])  # missing width_field, height_field

    result = await slop_studio.templates.add_template("bad_res", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "width_field" in result["error"]


@pytest.mark.anyio
async def test_add_template_rejects_invalid_aspect_ratios(templates_dir):
    meta = _sample_meta(aspect_ratios={"1:1": {"width": "not_int", "height": 1024}})

    result = await slop_studio.templates.add_template("bad_ar", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "integer" in result["error"].lower()


@pytest.mark.anyio
async def test_add_template_rejects_non_dict_workflow(templates_dir):
    result = await slop_studio.templates.add_template("bad_wf", "not a dict", _sample_meta())

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "workflow_json must be a JSON object" in result["error"]


@pytest.mark.anyio
async def test_add_template_then_list_shows_it(templates_dir):
    await slop_studio.templates.add_template("new_one", SAMPLE_WORKFLOW, _sample_meta())

    result = await slop_studio.templates.list_templates()

    assert result["status"] == "success"
    names = [t["name"] for t in result["templates"]]
    assert "new_one" in names


@pytest.mark.anyio
async def test_add_template_storage_error(templates_dir, monkeypatch):
    from pathlib import Path

    original_write_text = Path.write_text

    def failing_write_text(self, *args, **kwargs):
        if self.suffix == ".json" and "meta" not in self.name:
            raise OSError("disk full")
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failing_write_text)

    result = await slop_studio.templates.add_template("fail_tpl", SAMPLE_WORKFLOW, _sample_meta())

    assert result["status"] == "error"
    assert result["error_type"] == "storage_error"
    assert result["retry_suggested"] is True


# ── update_template tests ──


@pytest.mark.anyio
async def test_update_template_overwrites_files(templates_dir):
    _write_meta(templates_dir, "existing")
    (templates_dir / "existing.json").write_text(json.dumps({"old": True}))

    new_workflow = {"new": True}
    new_meta = _sample_meta(description="Updated description")
    result = await slop_studio.templates.update_template("existing", new_workflow, new_meta)

    assert result["status"] == "success"
    workflow = json.loads((templates_dir / "existing.json").read_text())
    assert workflow == new_workflow
    meta = json.loads((templates_dir / "existing.meta.json").read_text())
    assert meta["description"] == "Updated description"


@pytest.mark.anyio
async def test_update_template_workflow_only(templates_dir):
    original_meta = _write_meta(templates_dir, "wf_only")
    (templates_dir / "wf_only.json").write_text(json.dumps({"old": True}))

    result = await slop_studio.templates.update_template("wf_only", workflow_json={"new": True})

    assert result["status"] == "success"
    workflow = json.loads((templates_dir / "wf_only.json").read_text())
    assert workflow == {"new": True}
    # Meta unchanged
    meta = json.loads((templates_dir / "wf_only.meta.json").read_text())
    assert meta["description"] == original_meta["description"]


@pytest.mark.anyio
async def test_update_template_metadata_only(templates_dir):
    _write_meta(templates_dir, "meta_only")
    (templates_dir / "meta_only.json").write_text(json.dumps({"original": True}))

    new_meta = _sample_meta(description="New desc")
    result = await slop_studio.templates.update_template("meta_only", metadata=new_meta)

    assert result["status"] == "success"
    # Workflow unchanged
    workflow = json.loads((templates_dir / "meta_only.json").read_text())
    assert workflow == {"original": True}
    meta = json.loads((templates_dir / "meta_only.meta.json").read_text())
    assert meta["description"] == "New desc"


@pytest.mark.anyio
async def test_update_template_nonexistent(templates_dir):
    result = await slop_studio.templates.update_template("nope", workflow_json={"a": 1})

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "not found" in result["error"]


@pytest.mark.anyio
@pytest.mark.parametrize("bad_name", ["../evil", "foo/bar", ".hidden", "a..b", "", "   "])
async def test_update_template_rejects_bad_names(templates_dir, bad_name):
    result = await slop_studio.templates.update_template(bad_name, workflow_json={"a": 1})

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"


@pytest.mark.anyio
async def test_update_template_requires_at_least_one(templates_dir):
    _write_meta(templates_dir, "existing")

    result = await slop_studio.templates.update_template("existing")

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "At least one" in result["error"]


@pytest.mark.anyio
async def test_mcp_tools_registered():
    import slop_studio.server

    importlib.reload(slop_studio.config)
    importlib.reload(slop_studio.templates)
    importlib.reload(slop_studio.server)
    from slop_studio.server import mcp

    tools = await mcp.list_tools()
    tool_names = [t.name for t in tools]
    assert "list_templates" in tool_names
    assert "get_template" in tool_names
    assert "add_template" in tool_names
    assert "update_template" in tool_names
    assert "delete_template" in tool_names
    assert "open_image" in tool_names


# ── delete_template tests ──


@pytest.mark.anyio
async def test_delete_template_removes_files(templates_dir):
    _write_meta(templates_dir, "to_delete")
    (templates_dir / "to_delete.json").write_text(json.dumps(SAMPLE_WORKFLOW))

    result = await slop_studio.templates.delete_template("to_delete")

    assert result["status"] == "success"
    assert not (templates_dir / "to_delete.json").exists()
    assert not (templates_dir / "to_delete.meta.json").exists()


@pytest.mark.anyio
async def test_delete_template_returns_success(templates_dir):
    _write_meta(templates_dir, "del_me")
    (templates_dir / "del_me.json").write_text(json.dumps(SAMPLE_WORKFLOW))

    result = await slop_studio.templates.delete_template("del_me")

    assert result == {"status": "success", "name": "del_me", "message": "Template 'del_me' deleted"}


@pytest.mark.anyio
async def test_delete_template_nonexistent(templates_dir):
    result = await slop_studio.templates.delete_template("nonexistent")

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "not found" in result["error"]
    assert result["retry_suggested"] is False


@pytest.mark.anyio
@pytest.mark.parametrize("bad_name", ["../evil", "foo/bar", ".hidden", "a..b", "", "   "])
async def test_delete_template_rejects_bad_names(templates_dir, bad_name):
    result = await slop_studio.templates.delete_template(bad_name)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert result["retry_suggested"] is False


@pytest.mark.anyio
async def test_delete_template_then_list_excludes_it(templates_dir):
    _write_meta(templates_dir, "keep_me")
    (templates_dir / "keep_me.json").write_text(json.dumps(SAMPLE_WORKFLOW))
    _write_meta(templates_dir, "remove_me")
    (templates_dir / "remove_me.json").write_text(json.dumps(SAMPLE_WORKFLOW))

    await slop_studio.templates.delete_template("remove_me")
    result = await slop_studio.templates.list_templates()

    names = [t["name"] for t in result["templates"]]
    assert "keep_me" in names
    assert "remove_me" not in names


@pytest.mark.anyio
async def test_delete_template_meta_only_succeeds(templates_dir):
    _write_meta(templates_dir, "meta_only")
    # No .json file — broken-template state

    result = await slop_studio.templates.delete_template("meta_only")

    assert result["status"] == "success"
    assert not (templates_dir / "meta_only.meta.json").exists()


@pytest.mark.anyio
async def test_delete_template_storage_error(templates_dir, monkeypatch):
    from pathlib import Path

    _write_meta(templates_dir, "fail_del")
    (templates_dir / "fail_del.json").write_text(json.dumps(SAMPLE_WORKFLOW))

    original_unlink = Path.unlink

    def failing_unlink(self, *args, **kwargs):
        if self.name == "fail_del.meta.json":
            raise OSError("permission denied")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failing_unlink)

    result = await slop_studio.templates.delete_template("fail_del")

    assert result["status"] == "error"
    assert result["error_type"] == "storage_error"
    assert result["retry_suggested"] is True


@pytest.mark.anyio
@pytest.mark.parametrize("bad_name", [None, 42, True, []])
async def test_delete_template_non_string_name(templates_dir, bad_name):
    result = await slop_studio.templates.delete_template(bad_name)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert result["retry_suggested"] is False


@pytest.mark.anyio
async def test_delete_template_workflow_file_error_still_succeeds(templates_dir, monkeypatch):
    # Partial failure: meta unlinks fine but workflow file raises OSError.
    # Template is gone (meta is canonical marker) so success is the correct response.
    from pathlib import Path

    _write_meta(templates_dir, "partial_del")
    (templates_dir / "partial_del.json").write_text(json.dumps(SAMPLE_WORKFLOW))

    original_unlink = Path.unlink

    def failing_unlink(self, *args, **kwargs):
        if self.name == "partial_del.json":
            raise OSError("permission denied")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failing_unlink)

    result = await slop_studio.templates.delete_template("partial_del")

    assert result["status"] == "success"
    assert not (templates_dir / "partial_del.meta.json").exists()
