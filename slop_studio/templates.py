import json
import logging
from pathlib import Path

from slop_studio.config import TEMPLATES_DIR
from slop_studio.errors import terminal_error, transient_error

logger = logging.getLogger(__name__)


def _validate_template_name(name: str) -> str | None:
    """Return error message if name is invalid, None if valid."""
    if not isinstance(name, str) or not name or not name.strip():
        return "Template name cannot be empty"
    if "/" in name:
        return f"Template name cannot contain '/': {name!r}"
    if ".." in name:
        return f"Template name cannot contain '..': {name!r}"
    if name.startswith("."):
        return f"Template name cannot start with '.': {name!r}"
    return None


def _validate_enum_input(key: str, defn: dict, all_inputs: dict) -> str | None:
    """Validate one ``input_type: "enum"`` input definition.

    An enum input maps a named choice onto a value for the input's own
    ``node_id``/``field`` plus, optionally, extra ``patches`` and text joined
    onto another input's prompt string. See ``backends.local._resolve_enum_inputs``
    for the runtime semantics.
    """
    options = defn.get("options")
    if not isinstance(options, dict) or not options:
        return f"Enum input '{key}' requires a non-empty 'options' JSON object"

    for option_name, option in options.items():
        if not isinstance(option_name, str) or not option_name:
            return f"Enum input '{key}' option names must be non-empty strings"
        if not isinstance(option, dict):
            return f"Enum input '{key}' option '{option_name}' must be a JSON object"
        for text_field in ("prompt_prefix", "prompt_suffix"):
            text = option.get(text_field)
            if text is not None and (not isinstance(text, str) or not text):
                return f"Enum input '{key}' option '{option_name}' '{text_field}' must be a non-empty string"
        patches = option.get("patches")
        if patches is not None:
            if not isinstance(patches, list):
                return f"Enum input '{key}' option '{option_name}' 'patches' must be a JSON array"
            for i, patch in enumerate(patches):
                if not isinstance(patch, dict):
                    return f"Enum input '{key}' option '{option_name}' patches[{i}] must be a JSON object"
                for patch_field in ("node_id", "field"):
                    value = patch.get(patch_field)
                    if not isinstance(value, str) or not value:
                        return (
                            f"Enum input '{key}' option '{option_name}' patches[{i}] "
                            f"missing required '{patch_field}' (string)"
                        )
                if "value" not in patch:
                    return f"Enum input '{key}' option '{option_name}' patches[{i}] missing required 'value'"

    default = defn.get("default")
    if default is not None and default not in options:
        return f"Enum input '{key}' default {default!r} is not one of its options: {sorted(options)}"

    prompt_input = defn.get("prompt_input")
    if prompt_input is not None and (not isinstance(prompt_input, str) or prompt_input not in all_inputs):
        return f"Enum input '{key}' prompt_input {prompt_input!r} does not name another input"

    # Prompt text has nowhere to go without prompt_input, and resolution would
    # skip it silently — the template would be accepted but not behave as written.
    if prompt_input is None:
        for option_name, option in options.items():
            if option.get("prompt_prefix") or option.get("prompt_suffix"):
                return (
                    f"Enum input '{key}' option '{option_name}' declares prompt text, "
                    f"so '{key}' must also declare 'prompt_input' naming the input to join it onto"
                )

    return None


def _validate_metadata(metadata: dict) -> str | None:
    """Validate meta structure. Returns error message or None.

    Required fields: ``name``, ``model``, ``description`` (non-empty strings).

    Optional structural fields: ``inputs`` (dict of input-definition dicts;
    entries with ``input_type: "enum"`` are validated by
    ``_validate_enum_input``),
    ``aspect_ratios`` (dict of ``{label: dims}`` where ``dims`` is a JSON
    object; ``width``/``height`` keys, when present, must be integers —
    ``bool`` is rejected), ``resolution_nodes`` (list of node-mapping
    dicts; each entry uses either the legacy ``width_field``/``height_field``
    pair or a ``field_map`` dict of ``{src_key: dest_field}`` for API-node
    string-field injection).

    Optional Story 6.6 fields (validated only when present):

    - ``backend``: string equal to ``"local"``, ``"cloud"``, or ``"either"``.
      Declares the template's intended backend; consumed by the router.
    - ``output_keys``: non-empty list of non-empty strings. Names the
      output node-keys for future multi-modal support (e.g. ``["images",
      "audio"]``). Not consumed yet — validated at write-time only.
    - ``cloud_estimate_credits``: non-negative ``int`` or ``float``.
      ``bool`` is rejected (mirrors the aspect_ratio pattern since
      ``isinstance(True, int)`` is truthy).
    - ``model_requirements``: list of objects declaring local-backend model
      dependencies. Each entry must have non-empty string ``filename``,
      ``subfolder``, ``url`` (with no ``..``/``/``/``\\`` in ``filename``
      or ``subfolder``). Optional: ``sha256`` (64-char hex string),
      ``size_bytes`` (non-negative ``int``; ``bool`` rejected), ``auth``
      ∈ ``{"huggingface", "civitai"}``.
    """
    missing = [f for f in ("name", "model", "description") if not isinstance(metadata.get(f), str) or not metadata[f]]
    if missing:
        return f"Missing required metadata fields: {', '.join(missing)}"

    inputs = metadata.get("inputs")
    if inputs is not None:
        if not isinstance(inputs, dict):
            return "inputs must be a JSON object"
        for key, defn in inputs.items():
            if not isinstance(defn, dict):
                return f"Input '{key}' must be a JSON object"
            if not isinstance(defn.get("node_id"), str) or not defn["node_id"]:
                return f"Input '{key}' missing required 'node_id' (string)"
            if not isinstance(defn.get("field"), str) or not defn["field"]:
                return f"Input '{key}' missing required 'field' (string)"
            if defn.get("input_type") == "enum":
                enum_err = _validate_enum_input(key, defn, inputs)
                if enum_err:
                    return enum_err

    aspect_ratios = metadata.get("aspect_ratios")
    if aspect_ratios is not None:
        if not isinstance(aspect_ratios, dict):
            return "aspect_ratios must be a JSON object"
        for label, dims in aspect_ratios.items():
            if not isinstance(dims, dict):
                return f"Aspect ratio '{label}' must be a JSON object"
            if "width" in dims and (isinstance(dims["width"], bool) or not isinstance(dims["width"], int)):
                return f"Aspect ratio '{label}' 'width' must be an integer"
            if "height" in dims and (isinstance(dims["height"], bool) or not isinstance(dims["height"], int)):
                return f"Aspect ratio '{label}' 'height' must be an integer"

    res_nodes = metadata.get("resolution_nodes")
    if res_nodes is not None:
        if not isinstance(res_nodes, list):
            return "resolution_nodes must be a JSON array"
        for i, node in enumerate(res_nodes):
            if not isinstance(node, dict):
                return f"resolution_nodes[{i}] must be a JSON object"
            if not isinstance(node.get("node_id"), str) or not node["node_id"]:
                return f"resolution_nodes[{i}] missing required 'node_id' (string)"
            has_field_map = "field_map" in node
            has_legacy = "width_field" in node or "height_field" in node
            if has_field_map and has_legacy:
                return (
                    f"resolution_nodes[{i}] cannot declare both 'field_map' and "
                    "'width_field'/'height_field' — pick one mode"
                )
            if has_field_map:
                field_map = node["field_map"]
                if not isinstance(field_map, dict) or not field_map:
                    return f"resolution_nodes[{i}] 'field_map' must be a non-empty JSON object"
                for src_key, dest_field in field_map.items():
                    if not isinstance(src_key, str) or not src_key:
                        return f"resolution_nodes[{i}] 'field_map' keys must be non-empty strings"
                    if not isinstance(dest_field, str) or not dest_field:
                        return f"resolution_nodes[{i}] 'field_map' values must be non-empty strings"
            else:
                for field in ("width_field", "height_field"):
                    if not isinstance(node.get(field), str) or not node[field]:
                        return f"resolution_nodes[{i}] missing required '{field}' (string)"

    backend = metadata.get("backend")
    if backend is not None and (not isinstance(backend, str) or backend not in ("local", "cloud", "either")):
        return f"backend must be one of: 'local', 'cloud', 'either'; got {backend!r}"

    output_keys = metadata.get("output_keys")
    if output_keys is not None:
        if not isinstance(output_keys, list) or not output_keys:
            return "output_keys must be a non-empty JSON array of strings"
        for i, key in enumerate(output_keys):
            if not isinstance(key, str) or not key:
                return f"output_keys[{i}] must be a non-empty string"

    credits = metadata.get("cloud_estimate_credits")
    if credits is not None:
        if isinstance(credits, bool) or not isinstance(credits, (int, float)):
            return f"cloud_estimate_credits must be a non-negative number; got {credits!r}"
        if credits < 0:
            return f"cloud_estimate_credits must be non-negative; got {credits}"

    model_requirements = metadata.get("model_requirements")
    if model_requirements is not None:
        if not isinstance(model_requirements, list):
            return "model_requirements must be a JSON array"
        for i, entry in enumerate(model_requirements):
            if not isinstance(entry, dict):
                return f"model_requirements[{i}] must be a JSON object"
            for required_field in ("filename", "subfolder", "url"):
                value = entry.get(required_field)
                if not isinstance(value, str) or not value:
                    return f"model_requirements[{i}] missing required '{required_field}' (non-empty string)"
            if not entry["url"].startswith("https://"):
                return (
                    f"model_requirements[{i}] 'url' must start with 'https://' "
                    f"(plain HTTP would expose auth tokens); got {entry['url']!r}"
                )
            for path_field in ("filename", "subfolder"):
                value = entry[path_field]
                if ".." in value or "/" in value or "\\" in value:
                    return (
                        f"model_requirements[{i}] '{path_field}' must not contain '..', '/', or '\\\\'; got {value!r}"
                    )
                if any(ord(c) < 32 for c in value):
                    return f"model_requirements[{i}] '{path_field}' must not contain control characters; got {value!r}"
            if entry["subfolder"] == ".":
                return f"model_requirements[{i}] 'subfolder' must not be '.'; got {entry['subfolder']!r}"
            sha256 = entry.get("sha256")
            if sha256 is not None:
                if not isinstance(sha256, str):
                    return f"model_requirements[{i}] 'sha256' must be a 64-character hex string"
                if len(sha256) != 64 or not all(c in "0123456789abcdefABCDEF" for c in sha256):
                    return f"model_requirements[{i}] 'sha256' must be a 64-character hex string"
            size_bytes = entry.get("size_bytes")
            if size_bytes is not None:
                if isinstance(size_bytes, bool) or not isinstance(size_bytes, int):
                    return f"model_requirements[{i}] 'size_bytes' must be a non-negative integer; got {size_bytes!r}"
                if size_bytes < 0:
                    return f"model_requirements[{i}] 'size_bytes' must be non-negative; got {size_bytes}"
            auth = entry.get("auth")
            if auth is not None and (not isinstance(auth, str) or auth not in ("huggingface", "civitai")):
                return f"model_requirements[{i}] 'auth' must be one of: 'huggingface', 'civitai'; got {auth!r}"

    return None


async def list_templates() -> dict:
    """List all available templates with summary metadata.

    Each entry exposes ``name``, ``model``, ``description``, ``aspect_ratios``
    (list of labels), ``expected_duration``, and ``backend`` (``"local"``
    when absent — Story 6.6 default). Invalid meta values are surfaced
    verbatim rather than normalized — validation happens at write-time.
    """
    templates_path = Path(TEMPLATES_DIR)
    if not templates_path.is_dir():
        return {"status": "success", "templates": []}

    templates = []
    for meta_file in sorted(templates_path.glob("*.meta.json")):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            templates.append(
                {
                    "name": meta["name"],
                    "model": meta["model"],
                    "description": meta["description"],
                    "aspect_ratios": list(meta.get("aspect_ratios", {}).keys()),
                    "expected_duration": meta.get("expected_duration", "unknown"),
                    "backend": meta.get("backend", "local"),
                }
            )
        except (json.JSONDecodeError, KeyError, OSError) as exc:
            logger.warning("Skipping invalid template %s: %s", meta_file.name, exc)
    return {"status": "success", "templates": templates}


async def get_template(template_name: str) -> dict:
    """Get full metadata for a specific template.

    Spreads the on-disk meta into the response and injects ``"backend":
    "local"`` when the field is absent (Story 6.6 default, mirroring
    ``list_templates``). ``output_keys`` and ``cloud_estimate_credits`` are
    NOT normalized — absent means absent in the response.
    """
    name_err = _validate_template_name(template_name)
    if name_err:
        return terminal_error("invalid_inputs", name_err)
    meta_path = Path(TEMPLATES_DIR) / f"{template_name}.meta.json"
    if not meta_path.is_file():
        return terminal_error("invalid_inputs", f"Template '{template_name}' not found")

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return terminal_error("read_error", f"Failed to read template '{template_name}': {exc}")
    return {**meta, "backend": meta.get("backend", "local"), "status": "success"}


async def add_template(name: str, workflow_json: dict, metadata: dict) -> dict:
    """Add a new workflow template with validation."""
    name_err = _validate_template_name(name)
    if name_err:
        return terminal_error("invalid_inputs", name_err)

    if not isinstance(workflow_json, dict):
        return terminal_error("invalid_inputs", "workflow_json must be a JSON object")

    metadata = {**metadata, "name": name}
    meta_err = _validate_metadata(metadata)
    if meta_err:
        return terminal_error("invalid_inputs", meta_err)

    templates_path = Path(TEMPLATES_DIR)
    workflow_path = templates_path / f"{name}.json"
    meta_path = templates_path / f"{name}.meta.json"

    if workflow_path.exists() or meta_path.exists():
        return terminal_error(
            "invalid_inputs",
            f"Template '{name}' already exists. Use update_template to modify it.",
        )

    try:
        templates_path.mkdir(parents=True, exist_ok=True)
        workflow_path.write_text(json.dumps(workflow_json, indent=2), encoding="utf-8")
        meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    except OSError as exc:
        try:
            workflow_path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
        except OSError:
            pass
        return transient_error("storage_error", f"Failed to write template '{name}': {exc}")

    logger.info("Template added: %s", name)
    return {"status": "success", "name": name, "message": f"Template '{name}' added"}


async def delete_template(name: str) -> dict:
    """Delete a workflow template by name."""
    name_err = _validate_template_name(name)
    if name_err:
        return terminal_error("invalid_inputs", name_err)

    templates_path = Path(TEMPLATES_DIR)
    meta_path = templates_path / f"{name}.meta.json"
    workflow_path = templates_path / f"{name}.json"

    if not meta_path.is_file():
        return terminal_error("invalid_inputs", f"Template '{name}' not found")

    try:
        meta_path.unlink()
    except OSError as exc:
        return transient_error("storage_error", f"Failed to delete template '{name}': {exc}")

    try:
        workflow_path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Template '%s' metadata deleted but workflow file could not be removed: %s", name, exc)

    logger.info("Template deleted: %s", name)
    return {"status": "success", "name": name, "message": f"Template '{name}' deleted"}


async def update_template(name: str, workflow_json: dict | None = None, metadata: dict | None = None) -> dict:
    """Update an existing workflow template."""
    name_err = _validate_template_name(name)
    if name_err:
        return terminal_error("invalid_inputs", name_err)

    meta_path = Path(TEMPLATES_DIR) / f"{name}.meta.json"
    if not meta_path.is_file():
        return terminal_error("invalid_inputs", f"Template '{name}' not found")

    if workflow_json is None and metadata is None:
        return terminal_error(
            "invalid_inputs",
            "At least one of workflow_json or metadata must be provided",
        )

    if workflow_json is not None:
        if not isinstance(workflow_json, dict):
            return terminal_error("invalid_inputs", "workflow_json must be a JSON object")
        workflow_path = Path(TEMPLATES_DIR) / f"{name}.json"
        try:
            workflow_path.write_text(json.dumps(workflow_json, indent=2), encoding="utf-8")
        except OSError as exc:
            return transient_error("storage_error", f"Failed to write workflow for '{name}': {exc}")

    if metadata is not None:
        metadata = {**metadata, "name": name}
        meta_err = _validate_metadata(metadata)
        if meta_err:
            return terminal_error("invalid_inputs", meta_err)
        try:
            meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        except OSError as exc:
            return transient_error("storage_error", f"Failed to write metadata for '{name}': {exc}")

    logger.info("Template updated: %s", name)
    return {"status": "success", "name": name, "message": f"Template '{name}' updated"}
