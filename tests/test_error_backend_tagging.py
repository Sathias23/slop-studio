"""Story 6.7 — locks the helper-level backend-tag contract.

Pure unit tests on ``slop_studio.errors.terminal_error`` /
``transient_error``. No fixtures; no I/O.
"""

from slop_studio.errors import terminal_error, transient_error


def test_terminal_error_without_backend_omits_key():
    result = terminal_error("invalid_inputs", "bad input")
    assert "backend" not in result
    assert set(result.keys()) == {"status", "error", "error_type", "retry_suggested"}


def test_terminal_error_with_backend_includes_key():
    result = terminal_error("no_credits", "out of credits", backend="cloud")
    assert result["backend"] == "cloud"
    assert set(result.keys()) == {"status", "error", "error_type", "retry_suggested", "backend"}


def test_transient_error_without_backend_omits_key():
    result = transient_error("unreachable", "timeout")
    assert "backend" not in result
    assert set(result.keys()) == {"status", "error", "error_type", "retry_suggested"}


def test_transient_error_with_backend_includes_key():
    result = transient_error("unreachable", "timeout", backend="local")
    assert result["backend"] == "local"
    assert set(result.keys()) == {"status", "error", "error_type", "retry_suggested", "backend"}


def test_new_reason_codes_accepted_by_terminal_error():
    for reason in ("auth_failed", "no_credits", "account_issue", "rate_limited"):
        result = terminal_error(reason, "test", backend="cloud")
        assert result["error_type"] == reason
        assert result["backend"] == "cloud"
        assert result["retry_suggested"] is False


def test_backend_value_persisted_verbatim():
    # No normalization, no whitelist — caller contract (same as error_type).
    result = terminal_error("foo", "bar", backend="weird-backend-name")
    assert result["backend"] == "weird-backend-name"


def test_backend_key_appears_after_retry_suggested_for_stable_ordering():
    # AC #2: backend field added AFTER retry_suggested for deterministic dict iteration.
    result = terminal_error("auth_failed", "msg", backend="cloud")
    keys = list(result.keys())
    assert keys.index("backend") == keys.index("retry_suggested") + 1


def test_default_backend_none_omits_key():
    # Explicit None is treated identically to absent kwarg.
    result = transient_error("unreachable", "x", backend=None)
    assert "backend" not in result
