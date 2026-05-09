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
        assert set(t.keys()) == {"name", "model", "description", "aspect_ratios", "expected_duration", "backend"}
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
async def test_list_templates_includes_backend_field_from_meta(templates_dir):
    _write_meta(templates_dir, "cloudy", backend="cloud")

    result = await slop_studio.templates.list_templates()

    assert result["status"] == "success"
    entry = next(t for t in result["templates"] if t["name"] == "cloudy")
    assert entry["backend"] == "cloud"


@pytest.mark.anyio
async def test_list_templates_defaults_backend_to_local_when_absent(templates_dir):
    # _write_meta does not set backend unless overridden — absent by default.
    _write_meta(templates_dir, "legacy")

    result = await slop_studio.templates.list_templates()

    assert result["status"] == "success"
    entry = next(t for t in result["templates"] if t["name"] == "legacy")
    assert entry["backend"] == "local"


@pytest.mark.anyio
async def test_starter_templates_all_declare_explicit_backend(templates_dir):
    """AC #18 canary — shipped starter templates must declare an explicit backend.

    Local GGUF templates are rejected by Comfy Cloud (VALIDATION_ERROR) and
    cloud API-node templates fail on an unmodified local ComfyUI; tagging
    every starter explicitly ("local" or "cloud") prevents silent regression
    whichever way SLOP_STUDIO_DEFAULT_BACKEND is set. "either" or absent
    means a starter would inherit the user's default — not safe.
    """
    import shutil
    from pathlib import Path

    starter_dir = Path(__file__).resolve().parent.parent / "slop_studio" / "assets" / "starter-templates"
    for meta_file in starter_dir.glob("*.meta.json"):
        shutil.copy(meta_file, templates_dir / meta_file.name)

    result = await slop_studio.templates.list_templates()

    assert result["status"] == "success"
    assert len(result["templates"]) >= 3
    for entry in result["templates"]:
        assert entry["backend"] in {"local", "cloud"}, (
            f"starter template '{entry['name']}' must declare backend=local or backend=cloud "
            f"(got: {entry['backend']!r})"
        )


def _starter_template_pairs():
    """Yield (name, meta_path, workflow_path) for every shipped starter template.

    Parametrization helper — surfaces one failing test per broken template
    rather than collapsing into a single opaque assertion.
    """
    from pathlib import Path

    starter_dir = Path(__file__).resolve().parent.parent / "slop_studio" / "assets" / "starter-templates"
    for meta_path in sorted(starter_dir.glob("*.meta.json")):
        name = meta_path.name.removesuffix(".meta.json")
        yield name, meta_path, starter_dir / f"{name}.json"


@pytest.mark.parametrize("name, meta_path, workflow_path", list(_starter_template_pairs()))
def test_starter_template_meta_matches_workflow(name, meta_path, workflow_path):
    """Every shipped starter template must have a workflow file whose nodes and
    fields match the node_id/field references declared in its sidecar.

    A silent drift between the .json and .meta.json (renamed node, stale field)
    doesn't trip the validator (which only checks meta-internal consistency) but
    would break queue_prompt at runtime. This canary catches the cross-file
    contract at CI time.
    """
    assert workflow_path.exists(), f"starter template '{name}' is missing its workflow .json"

    meta = json.loads(meta_path.read_text())
    workflow = json.loads(workflow_path.read_text())

    assert meta.get("name") == name, f"meta 'name' field ({meta.get('name')!r}) must match filename stem ({name!r})"

    err = slop_studio.templates._validate_metadata(meta)
    assert err is None, f"{name}: _validate_metadata failed: {err}"

    for input_name, input_def in (meta.get("inputs") or {}).items():
        node_id = input_def["node_id"]
        field = input_def["field"]
        assert node_id in workflow, (
            f"{name}: input '{input_name}' references node_id {node_id!r}, which is not a key in {workflow_path.name}"
        )
        node_inputs = workflow[node_id].get("inputs", {})
        assert field in node_inputs, (
            f"{name}: input '{input_name}' references field {field!r} on node "
            f"{node_id}, but that field is not present in {workflow_path.name}"
        )

    for i, res_node in enumerate(meta.get("resolution_nodes") or []):
        node_id = res_node["node_id"]
        assert node_id in workflow, (
            f"{name}: resolution_nodes[{i}] references node_id {node_id!r}, which is not a key in {workflow_path.name}"
        )
        node_inputs = workflow[node_id].get("inputs", {})
        if "field_map" in res_node:
            aspect_ratios = meta.get("aspect_ratios") or {}
            for dest_field in res_node["field_map"].values():
                assert dest_field in node_inputs, (
                    f"{name}: resolution_nodes[{i}] field_map targets field "
                    f"{dest_field!r} on node {node_id}, but that field is "
                    f"not present in {workflow_path.name}"
                )
            for src_key in res_node["field_map"]:
                for label, dims in aspect_ratios.items():
                    assert src_key in dims, (
                        f"{name}: aspect_ratios[{label!r}] is missing key "
                        f"{src_key!r} required by resolution_nodes[{i}].field_map"
                    )
        else:
            for fk in ("width_field", "height_field"):
                assert res_node[fk] in node_inputs, (
                    f"{name}: resolution_nodes[{i}].{fk} targets field "
                    f"{res_node[fk]!r} on node {node_id}, but that field is "
                    f"not present in {workflow_path.name}"
                )


@pytest.mark.parametrize(
    "name",
    ["api_openai_gpt_image_2_t2i", "api_openai_gpt_image_2_image_edit"],
)
@pytest.mark.parametrize(
    "aspect_ratio, expected_size",
    [
        ("1:1", "1024x1024"),
        ("3:2", "1536x1024"),
        ("2:3", "1024x1536"),
        ("16:9", "1536x1024"),
        ("9:16", "1024x1536"),
        ("21:9", "1536x1024"),
    ],
)
def test_gpt_image_2_aspect_ratio_injection(name, aspect_ratio, expected_size):
    """Exercise _inject_resolution against the shipped GPT Image 2 sidecars.

    Covers the field_map path for API nodes whose size input is a literal
    'WIDTHxHEIGHT' string (distinct from integer width/height pairs and from
    Gemini's 'aspect_ratio: "3:4"' convention). Keeps the sidecar → injector
    contract honest across label → size mapping changes.
    """
    from pathlib import Path

    from slop_studio.backends.local import _inject_resolution

    starter_dir = Path(__file__).resolve().parent.parent / "slop_studio" / "assets" / "starter-templates"
    meta = json.loads((starter_dir / f"{name}.meta.json").read_text())
    workflow = json.loads((starter_dir / f"{name}.json").read_text())

    _inject_resolution(workflow, meta, aspect_ratio)

    assert workflow["268"]["inputs"]["size"] == expected_size


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
async def test_get_template_surfaces_backend_when_present(templates_dir):
    _write_meta(templates_dir, "flex", backend="either")

    result = await slop_studio.templates.get_template("flex")

    assert result["status"] == "success"
    assert result["backend"] == "either"


@pytest.mark.anyio
async def test_get_template_surfaces_output_keys_when_present(templates_dir):
    _write_meta(templates_dir, "multi", output_keys=["images", "audio"])

    result = await slop_studio.templates.get_template("multi")

    assert result["status"] == "success"
    assert result["output_keys"] == ["images", "audio"]


@pytest.mark.anyio
async def test_get_template_surfaces_cloud_estimate_credits_when_present(templates_dir):
    _write_meta(templates_dir, "pricey", cloud_estimate_credits=12)

    result = await slop_studio.templates.get_template("pricey")

    assert result["status"] == "success"
    assert result["cloud_estimate_credits"] == 12
    assert isinstance(result["cloud_estimate_credits"], int)


@pytest.mark.anyio
async def test_get_template_defaults_backend_to_local_when_absent(templates_dir):
    _write_meta(templates_dir, "legacy")

    result = await slop_studio.templates.get_template("legacy")

    assert result["status"] == "success"
    assert result["backend"] == "local"


@pytest.mark.anyio
async def test_get_template_does_not_normalize_output_keys_when_absent(templates_dir):
    _write_meta(templates_dir, "legacy")

    result = await slop_studio.templates.get_template("legacy")

    assert result["status"] == "success"
    assert "output_keys" not in result
    assert "cloud_estimate_credits" not in result


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


# ── field_map resolution_nodes (Gemini-style API nodes) ──


@pytest.mark.anyio
async def test_add_template_accepts_field_map_resolution_nodes(templates_dir):
    meta = _sample_meta(
        aspect_ratios={"1:1": {"aspect_ratio": "1:1"}, "16:9": {"aspect_ratio": "16:9"}},
        resolution_nodes=[{"node_id": "35", "field_map": {"aspect_ratio": "aspect_ratio"}}],
    )

    result = await slop_studio.templates.add_template("ok_fieldmap", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "success"


@pytest.mark.anyio
async def test_add_template_rejects_field_map_with_legacy_fields(templates_dir):
    meta = _sample_meta(
        aspect_ratios={"1:1": {"aspect_ratio": "1:1"}},
        resolution_nodes=[
            {
                "node_id": "35",
                "field_map": {"aspect_ratio": "aspect_ratio"},
                "width_field": "width",
                "height_field": "height",
            }
        ],
    )

    result = await slop_studio.templates.add_template("bad_mixed", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert "pick one mode" in result["error"]


@pytest.mark.anyio
async def test_add_template_rejects_empty_field_map(templates_dir):
    meta = _sample_meta(
        aspect_ratios={"1:1": {"aspect_ratio": "1:1"}},
        resolution_nodes=[{"node_id": "35", "field_map": {}}],
    )

    result = await slop_studio.templates.add_template("bad_empty_fm", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert "non-empty" in result["error"]


@pytest.mark.anyio
async def test_add_template_accepts_aspect_ratios_without_width_height(templates_dir):
    meta = _sample_meta(
        aspect_ratios={"3:4": {"aspect_ratio": "3:4"}},
        resolution_nodes=[{"node_id": "35", "field_map": {"aspect_ratio": "aspect_ratio"}}],
    )

    result = await slop_studio.templates.add_template("ok_no_wh", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "success"


# ── Story 6.6: backend / output_keys / cloud_estimate_credits validation ──


@pytest.mark.anyio
@pytest.mark.parametrize("backend_value", ["local", "cloud", "either"])
async def test_add_template_accepts_valid_backend(templates_dir, backend_value):
    meta = _sample_meta(backend=backend_value)

    result = await slop_studio.templates.add_template(f"bk_{backend_value}", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "success"
    on_disk = json.loads((templates_dir / f"bk_{backend_value}.meta.json").read_text())
    assert on_disk["backend"] == backend_value


@pytest.mark.anyio
async def test_add_template_rejects_invalid_backend(templates_dir):
    meta = _sample_meta(backend="kloud")

    result = await slop_studio.templates.add_template("bad_backend", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "backend" in result["error"]
    assert "local" in result["error"] and "cloud" in result["error"] and "either" in result["error"]
    assert not (templates_dir / "bad_backend.json").exists()
    assert not (templates_dir / "bad_backend.meta.json").exists()


@pytest.mark.anyio
async def test_add_template_rejects_non_string_backend(templates_dir):
    meta = _sample_meta(backend=42)

    result = await slop_studio.templates.add_template("num_backend", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "backend" in result["error"]


@pytest.mark.anyio
async def test_add_template_accepts_output_keys(templates_dir):
    meta = _sample_meta(output_keys=["images"])

    result = await slop_studio.templates.add_template("ok_single", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "success"
    on_disk = json.loads((templates_dir / "ok_single.meta.json").read_text())
    assert on_disk["output_keys"] == ["images"]


@pytest.mark.anyio
async def test_add_template_accepts_output_keys_multiple(templates_dir):
    meta = _sample_meta(output_keys=["images", "audio"])

    result = await slop_studio.templates.add_template("ok_multi", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "success"
    on_disk = json.loads((templates_dir / "ok_multi.meta.json").read_text())
    assert on_disk["output_keys"] == ["images", "audio"]


@pytest.mark.anyio
async def test_add_template_rejects_empty_output_keys(templates_dir):
    meta = _sample_meta(output_keys=[])

    result = await slop_studio.templates.add_template("ok_empty", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "output_keys" in result["error"]


@pytest.mark.anyio
async def test_add_template_rejects_output_keys_non_list(templates_dir):
    meta = _sample_meta(output_keys="images")

    result = await slop_studio.templates.add_template("ok_str", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "output_keys" in result["error"]


@pytest.mark.anyio
async def test_add_template_rejects_output_keys_non_string_entry(templates_dir):
    meta = _sample_meta(output_keys=[42])

    result = await slop_studio.templates.add_template("ok_int_entry", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "output_keys" in result["error"]


@pytest.mark.anyio
async def test_add_template_rejects_output_keys_empty_string_entry(templates_dir):
    meta = _sample_meta(output_keys=[""])

    result = await slop_studio.templates.add_template("ok_blank_entry", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "output_keys" in result["error"]


@pytest.mark.anyio
@pytest.mark.parametrize("credits_value", [12, 12.5, 0])
async def test_add_template_accepts_valid_cloud_estimate_credits(templates_dir, credits_value):
    meta = _sample_meta(cloud_estimate_credits=credits_value)

    result = await slop_studio.templates.add_template(f"cr_{type(credits_value).__name__}", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "success"
    on_disk = json.loads((templates_dir / f"cr_{type(credits_value).__name__}.meta.json").read_text())
    assert on_disk["cloud_estimate_credits"] == credits_value
    assert type(on_disk["cloud_estimate_credits"]) is type(credits_value)


@pytest.mark.anyio
async def test_add_template_rejects_negative_cloud_estimate_credits(templates_dir):
    meta = _sample_meta(cloud_estimate_credits=-1)

    result = await slop_studio.templates.add_template("cr_neg", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "cloud_estimate_credits" in result["error"]


@pytest.mark.anyio
async def test_add_template_rejects_cloud_estimate_credits_bool(templates_dir):
    # bool is a subclass of int in Python — the aspect_ratio pattern
    # explicitly excludes bool, and so must this validator.
    meta = _sample_meta(cloud_estimate_credits=True)

    result = await slop_studio.templates.add_template("cr_bool", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "cloud_estimate_credits" in result["error"]


@pytest.mark.anyio
async def test_add_template_rejects_cloud_estimate_credits_string(templates_dir):
    meta = _sample_meta(cloud_estimate_credits="12")

    result = await slop_studio.templates.add_template("cr_str", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "cloud_estimate_credits" in result["error"]


# ── model_requirements validation ──


_VALID_MODEL_REQ = {
    "filename": "flux-2-klein-9b-Q8_0.gguf",
    "subfolder": "unet",
    "url": "https://huggingface.co/example/flux-2-klein-9b-Q8_0.gguf",
    "sha256": "a" * 64,
    "size_bytes": 9876543210,
    "auth": "huggingface",
}


@pytest.mark.anyio
async def test_add_template_accepts_valid_model_requirements(templates_dir):
    meta = _sample_meta(model_requirements=[dict(_VALID_MODEL_REQ)])

    result = await slop_studio.templates.add_template("ok_mr_full", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "success"
    on_disk = json.loads((templates_dir / "ok_mr_full.meta.json").read_text())
    assert on_disk["model_requirements"][0]["filename"] == _VALID_MODEL_REQ["filename"]


@pytest.mark.anyio
async def test_add_template_accepts_minimal_model_requirements(templates_dir):
    meta = _sample_meta(
        model_requirements=[
            {
                "filename": "model.safetensors",
                "subfolder": "checkpoints",
                "url": "https://example.com/model.safetensors",
            }
        ]
    )

    result = await slop_studio.templates.add_template("ok_mr_min", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "success"


@pytest.mark.anyio
async def test_add_template_accepts_empty_model_requirements(templates_dir):
    meta = _sample_meta(model_requirements=[])

    result = await slop_studio.templates.add_template("ok_mr_empty", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "success"


@pytest.mark.anyio
async def test_add_template_rejects_model_requirements_non_list(templates_dir):
    meta = _sample_meta(model_requirements={"filename": "foo"})

    result = await slop_studio.templates.add_template("bad_mr_obj", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "model_requirements" in result["error"]


@pytest.mark.anyio
@pytest.mark.parametrize("missing_field", ["filename", "subfolder", "url"])
async def test_add_template_rejects_model_requirements_missing_required_field(templates_dir, missing_field):
    entry = dict(_VALID_MODEL_REQ)
    del entry[missing_field]
    meta = _sample_meta(model_requirements=[entry])

    result = await slop_studio.templates.add_template(f"bad_mr_no_{missing_field}", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert missing_field in result["error"]


@pytest.mark.anyio
@pytest.mark.parametrize("empty_field", ["filename", "subfolder", "url"])
async def test_add_template_rejects_model_requirements_empty_required_field(templates_dir, empty_field):
    entry = dict(_VALID_MODEL_REQ)
    entry[empty_field] = ""
    meta = _sample_meta(model_requirements=[entry])

    result = await slop_studio.templates.add_template(f"bad_mr_empty_{empty_field}", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert empty_field in result["error"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "field, bad_value",
    [
        ("filename", "../evil.gguf"),
        ("filename", "sub/evil.gguf"),
        ("filename", "sub\\evil.gguf"),
        ("subfolder", "../unet"),
        ("subfolder", "unet/sub"),
        ("subfolder", "unet\\sub"),
    ],
)
async def test_add_template_rejects_model_requirements_path_traversal(templates_dir, field, bad_value):
    entry = dict(_VALID_MODEL_REQ)
    entry[field] = bad_value
    meta = _sample_meta(model_requirements=[entry])

    result = await slop_studio.templates.add_template("bad_mr_traversal", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert field in result["error"]
    assert not (templates_dir / "bad_mr_traversal.meta.json").exists()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "bad_url",
    [
        "http://example.com/model.gguf",
        "ftp://example.com/model.gguf",
        "//example.com/model.gguf",
        "example.com/model.gguf",
    ],
)
async def test_add_template_rejects_non_https_url(templates_dir, bad_url):
    """Plain HTTP (or any non-https scheme) must be rejected at write time so
    auth tokens can never be sent over an insecure transport."""
    entry = dict(_VALID_MODEL_REQ)
    entry["url"] = bad_url
    meta = _sample_meta(model_requirements=[entry])

    result = await slop_studio.templates.add_template("bad_scheme", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "https://" in result["error"]
    assert not (templates_dir / "bad_scheme.meta.json").exists()


@pytest.mark.anyio
async def test_add_template_rejects_model_requirements_filename_with_nul(templates_dir):
    """NUL byte in filename → invalid_inputs (control-character rejection)."""
    entry = dict(_VALID_MODEL_REQ)
    entry["filename"] = "model\x00.gguf"
    meta = _sample_meta(model_requirements=[entry])

    result = await slop_studio.templates.add_template("bad_mr_nul_fn", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "filename" in result["error"]
    assert "control" in result["error"].lower()
    assert not (templates_dir / "bad_mr_nul_fn.meta.json").exists()


@pytest.mark.anyio
async def test_add_template_rejects_model_requirements_subfolder_with_control_char(templates_dir):
    """Control byte (\\x01) in subfolder → invalid_inputs."""
    entry = dict(_VALID_MODEL_REQ)
    entry["subfolder"] = "unet\x01"
    meta = _sample_meta(model_requirements=[entry])

    result = await slop_studio.templates.add_template("bad_mr_ctl_sub", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "subfolder" in result["error"]
    assert "control" in result["error"].lower()
    assert not (templates_dir / "bad_mr_ctl_sub.meta.json").exists()


@pytest.mark.anyio
async def test_add_template_rejects_model_requirements_subfolder_dot(templates_dir):
    """``subfolder == "."`` would resolve to models_dir/ root → reject."""
    entry = dict(_VALID_MODEL_REQ)
    entry["subfolder"] = "."
    meta = _sample_meta(model_requirements=[entry])

    result = await slop_studio.templates.add_template("bad_mr_dot_sub", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "subfolder" in result["error"]
    assert not (templates_dir / "bad_mr_dot_sub.meta.json").exists()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "bad_sha",
    [
        "a" * 63,  # too short
        "a" * 65,  # too long
        "g" * 64,  # not hex
        "Z" * 64,  # not hex
        12345,  # not a string
    ],
)
async def test_add_template_rejects_model_requirements_bad_sha256(templates_dir, bad_sha):
    entry = dict(_VALID_MODEL_REQ)
    entry["sha256"] = bad_sha
    meta = _sample_meta(model_requirements=[entry])

    result = await slop_studio.templates.add_template("bad_mr_sha", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "sha256" in result["error"]


@pytest.mark.anyio
async def test_add_template_rejects_model_requirements_negative_size_bytes(templates_dir):
    entry = dict(_VALID_MODEL_REQ)
    entry["size_bytes"] = -1
    meta = _sample_meta(model_requirements=[entry])

    result = await slop_studio.templates.add_template("bad_mr_neg_size", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "size_bytes" in result["error"]


@pytest.mark.anyio
async def test_add_template_rejects_model_requirements_bool_size_bytes(templates_dir):
    # bool is a subclass of int — explicitly reject it (mirrors cloud_estimate_credits).
    entry = dict(_VALID_MODEL_REQ)
    entry["size_bytes"] = True
    meta = _sample_meta(model_requirements=[entry])

    result = await slop_studio.templates.add_template("bad_mr_bool_size", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "size_bytes" in result["error"]


@pytest.mark.anyio
async def test_add_template_rejects_model_requirements_bad_auth(templates_dir):
    entry = dict(_VALID_MODEL_REQ)
    entry["auth"] = "google"
    meta = _sample_meta(model_requirements=[entry])

    result = await slop_studio.templates.add_template("bad_mr_auth", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "auth" in result["error"]


@pytest.mark.anyio
async def test_add_template_rejects_model_requirements_non_dict_entry(templates_dir):
    meta = _sample_meta(model_requirements=["not a dict"])

    result = await slop_studio.templates.add_template("bad_mr_str_entry", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "model_requirements" in result["error"]


@pytest.mark.anyio
async def test_add_template_rejects_invalid_metadata_writes_no_file(templates_dir):
    """AC #11 canary — rejected add_template leaves the filesystem untouched."""
    meta = _sample_meta(backend="banana")

    result = await slop_studio.templates.add_template("ghost", SAMPLE_WORKFLOW, meta)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert not (templates_dir / "ghost.json").exists()
    assert not (templates_dir / "ghost.meta.json").exists()


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
async def test_update_template_rejects_invalid_new_field_without_mutating_disk(templates_dir):
    """AC #12 — invalid update must reject cleanly without mutating on-disk meta."""
    _write_meta(templates_dir, "existing")
    original_meta = json.loads((templates_dir / "existing.meta.json").read_text())

    result = await slop_studio.templates.update_template(
        "existing",
        metadata=_sample_meta(backend="kloud"),
    )

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_inputs"
    assert "backend" in result["error"]
    on_disk = json.loads((templates_dir / "existing.meta.json").read_text())
    assert "backend" not in on_disk
    assert on_disk == original_meta


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
    assert "open_gallery" in tool_names


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
