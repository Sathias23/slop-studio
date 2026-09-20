"""Effective-value export on submission (local and cloud paths).

Every successful submission reports the seeds that were actually written and
a hash of the graph that was actually POSTed, so a caller can record real
provenance instead of re-deriving it from the source template later.
"""

import hashlib
import importlib
import json

import httpx
import pytest
import respx

import slop_studio.backends.router as router
import slop_studio.comfyui
import slop_studio.config
from slop_studio.backends.base import Backend
from slop_studio.backends.local import LocalBackend, _provenance, _seed_map, _workflow_sha256
from tests.test_comfyui import SAMPLE_META, SAMPLE_WORKFLOW, write_template

COMFYUI_URL = "http://test-comfyui:8188"

SEEDLESS_WORKFLOW = {
    "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["4", 1]}},
}


def canonical_sha256(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


@pytest.fixture
def templates_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOP_STUDIO_TEMPLATES_DIR", str(tmp_path))
    monkeypatch.setenv("COMFYUI_URL", COMFYUI_URL)
    importlib.reload(slop_studio.config)
    importlib.reload(slop_studio.comfyui)
    # router binds TEMPLATES_DIR at import; patch the binding rather than reloading
    # the module, which would break LocalBackend identity for other tests.
    monkeypatch.setattr(router, "TEMPLATES_DIR", str(tmp_path))
    write_template(tmp_path, "test_template", SAMPLE_WORKFLOW, SAMPLE_META)
    write_template(tmp_path, "seedless_template", SEEDLESS_WORKFLOW, SAMPLE_META)
    yield tmp_path
    importlib.reload(slop_studio.config)
    importlib.reload(slop_studio.comfyui)


class RecordingBackend(Backend):
    """Cloud-shaped backend that keeps the exact graph handed to submit()."""

    name = "cloud"

    def __init__(self):
        self.submitted = None

    async def submit(self, workflow: dict) -> dict:
        self.submitted = json.loads(json.dumps(workflow))
        return {"status": "success", "prompt_id": "cloud-native-1"}

    async def status(self, prompt_id: str) -> dict:  # pragma: no cover - unused
        return {"state": "pending"}

    async def history(self, prompt_id: str) -> dict:  # pragma: no cover - unused
        return {}

    async def view(self, filename: str, subfolder: str = "", file_type: str = "output") -> bytes:
        raise NotImplementedError  # pragma: no cover - unused

    async def upload_asset(self, file_path: str) -> str:
        raise NotImplementedError  # pragma: no cover - unused


@pytest.mark.anyio
@respx.mock
async def test_local_queue_prompt_exports_seeds_matching_the_posted_graph(templates_dir):
    respx.post(f"{COMFYUI_URL}/prompt").mock(
        return_value=httpx.Response(200, json={"prompt_id": "abc-123", "number": 1, "node_errors": {}})
    )
    result = await slop_studio.comfyui.queue_prompt("test_template", {"prompt": "hello"}, aspect_ratio="16:9")

    posted = json.loads(respx.calls.last.request.content)["prompt"]
    assert result["status"] == "success"
    assert result["effective_seeds"] == {"3": {"seed": posted["3"]["inputs"]["seed"]}}
    assert result["effective_seeds"] != {"3": {"seed": SAMPLE_WORKFLOW["3"]["inputs"]["seed"]}}
    assert result["submitted_workflow_sha256"] == canonical_sha256(posted)
    # The graph itself stays out of the response; the saved PNG carries it.
    assert "submitted_workflow" not in result


@pytest.mark.anyio
@respx.mock
async def test_local_backend_submit_exports_the_graph_it_posted(templates_dir):
    respx.post(f"{COMFYUI_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "abc-123"}))
    workflow = json.loads(json.dumps(SAMPLE_WORKFLOW))
    workflow["3"]["inputs"]["seed"] = 987654321
    result = await LocalBackend().submit(workflow)

    posted = json.loads(respx.calls.last.request.content)["prompt"]
    assert result["effective_seeds"] == {"3": {"seed": 987654321}}
    assert result["submitted_workflow_sha256"] == canonical_sha256(posted)


@pytest.mark.anyio
async def test_cloud_path_exports_the_same_two_fields(templates_dir):
    backend = RecordingBackend()
    result = await router._prepare_and_submit(backend, "test_template", {"prompt": "hello"}, "16:9")

    assert result["status"] == "success"
    assert result["effective_seeds"] == {"3": {"seed": backend.submitted["3"]["inputs"]["seed"]}}
    assert result["submitted_workflow_sha256"] == canonical_sha256(backend.submitted)


@pytest.mark.anyio
async def test_router_forwards_export_fields_alongside_the_prefixed_prompt_id(templates_dir, monkeypatch):
    backend = RecordingBackend()
    monkeypatch.setattr(router, "_resolve_cloud_backend", lambda: backend)
    result = await router.route_submission("test_template", {"prompt": "hello"}, backend_override="cloud")

    assert result["prompt_id"] == "cloud:cloud-native-1"
    assert result["effective_seeds"] == {"3": {"seed": backend.submitted["3"]["inputs"]["seed"]}}
    assert result["submitted_workflow_sha256"] == canonical_sha256(backend.submitted)


@pytest.mark.anyio
@respx.mock
async def test_local_route_submission_exports_both_fields_with_the_prefixed_id(templates_dir, monkeypatch):
    """The route Cenobite's queue_prompt actually takes: a local-declared template."""
    write_template(templates_dir, "local_tmpl", SAMPLE_WORKFLOW, {**SAMPLE_META, "backend": "local"})
    monkeypatch.setattr(router, "TEMPLATES_DIR", str(templates_dir))
    respx.post(f"{COMFYUI_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "native-abc"}))
    result = await router.route_submission("local_tmpl", {"prompt": "hello"}, aspect_ratio="1:1")

    posted = json.loads(respx.calls.last.request.content)["prompt"]
    assert result["prompt_id"] == "local:native-abc"
    assert result["effective_seeds"] == {"3": {"seed": posted["3"]["inputs"]["seed"]}}
    assert result["submitted_workflow_sha256"] == canonical_sha256(posted)


@pytest.mark.anyio
async def test_failed_cloud_submission_exports_nothing(templates_dir):
    class FailingBackend(RecordingBackend):
        async def submit(self, workflow: dict) -> dict:
            self.submitted = workflow
            return {"status": "error", "reason": "unreachable", "message": "down"}

    result = await router._prepare_and_submit(FailingBackend(), "test_template", {"prompt": "hello"}, None)
    assert result["status"] == "error"
    assert "effective_seeds" not in result
    assert "submitted_workflow_sha256" not in result


@pytest.mark.anyio
@respx.mock
async def test_template_without_a_seed_node_exports_an_empty_map(templates_dir):
    respx.post(f"{COMFYUI_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "abc-123"}))
    result = await slop_studio.comfyui.queue_prompt("seedless_template", {"prompt": "hello"})

    assert result["status"] == "success"
    assert result["effective_seeds"] == {}
    assert result["submitted_workflow_sha256"] == canonical_sha256(
        json.loads(respx.calls.last.request.content)["prompt"]
    )


def test_workflow_hash_is_stable_under_key_reordering():
    reordered = {}
    for node_id in sorted(SAMPLE_WORKFLOW, reverse=True):
        node = SAMPLE_WORKFLOW[node_id]
        reordered[node_id] = {
            key: ({k: node[key][k] for k in sorted(node[key], reverse=True)} if key == "inputs" else node[key])
            for key in sorted(node, reverse=True)
        }
    assert list(reordered) != list(SAMPLE_WORKFLOW)
    assert list(reordered["3"]["inputs"]) != list(SAMPLE_WORKFLOW["3"]["inputs"])
    assert _workflow_sha256(reordered) == _workflow_sha256(SAMPLE_WORKFLOW)
    assert _workflow_sha256(SAMPLE_WORKFLOW) == canonical_sha256(SAMPLE_WORKFLOW)


def test_unserialisable_graph_omits_the_hash_rather_than_raising():
    graph = {"3": {"class_type": float("nan"), "inputs": {"seed": 5}}}
    assert _workflow_sha256(graph) is None
    assert _provenance(graph) == {"effective_seeds": {"3": {"seed": 5}}}


def test_seed_map_ignores_non_integer_boolean_and_malformed_nodes():
    assert _seed_map({"a": {"inputs": {"seed": "not-an-int"}}, "b": "not-a-node", "c": {}}) == {}
    # Booleans are ints in Python; the Cenobite PNG reader excludes them, so this must too.
    assert _seed_map({"a": {"inputs": {"seed": True}}, "b": {"inputs": {"noise_seed": False}}}) == {}
    assert _seed_map({"25": {"inputs": {"noise_seed": 7, "steps": 8}}}) == {"25": {"noise_seed": 7}}


# Pinned canonicalisation fixture: sorted keys, no whitespace, ensure_ascii=False, no NaN.
# Cenobite hashes the executed graph independently; both sides must agree byte for
# byte. integration/test_provenance.py pins the same graph and the same digest.
NON_ASCII_GRAPH = {
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "un couloir — 静かな廊下 \u00e9\u0301"}},
    "25": {"class_type": "RandomNoise", "inputs": {"noise_seed": 7}},
}
NON_ASCII_SHA256 = "adbb2449177b87d36b5be4eaa26b5bebd37d09f9757f51f2fa9a522614d3814b"


def test_canonical_recipe_is_pinned_on_non_ascii_text():
    assert _workflow_sha256(NON_ASCII_GRAPH) == NON_ASCII_SHA256
    assert canonical_sha256(NON_ASCII_GRAPH) == NON_ASCII_SHA256
