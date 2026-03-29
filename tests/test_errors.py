from comfyclaude.errors import transient_error, terminal_error


def test_transient_error_returns_dict_with_all_fields():
    result = transient_error("unreachable", "Cannot connect to ComfyUI")
    assert set(result.keys()) == {"status", "error", "error_type", "retry_suggested"}


def test_transient_error_sets_retry_true_and_status_error():
    result = transient_error("unreachable", "Cannot connect to ComfyUI")
    assert result["status"] == "error"
    assert result["retry_suggested"] is True


def test_transient_error_preserves_message_and_type():
    result = transient_error("unreachable", "Cannot connect to ComfyUI")
    assert result["error"] == "Cannot connect to ComfyUI"
    assert result["error_type"] == "unreachable"


def test_transient_error_exact_output():
    result = transient_error("unreachable", "Cannot connect to ComfyUI")
    assert result == {
        "status": "error",
        "error": "Cannot connect to ComfyUI",
        "error_type": "unreachable",
        "retry_suggested": True,
    }


def test_terminal_error_returns_dict_with_all_fields():
    result = terminal_error("invalid_workflow", "Node type not found")
    assert set(result.keys()) == {"status", "error", "error_type", "retry_suggested"}


def test_terminal_error_sets_retry_false_and_status_error():
    result = terminal_error("invalid_workflow", "Node type not found")
    assert result["status"] == "error"
    assert result["retry_suggested"] is False


def test_terminal_error_preserves_message_and_type():
    result = terminal_error("invalid_workflow", "Node type not found")
    assert result["error"] == "Node type not found"
    assert result["error_type"] == "invalid_workflow"


def test_terminal_error_exact_output():
    result = terminal_error("invalid_workflow", "Node type not found")
    assert result == {
        "status": "error",
        "error": "Node type not found",
        "error_type": "invalid_workflow",
        "retry_suggested": False,
    }
