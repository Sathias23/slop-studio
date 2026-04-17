"""Error-response helpers for slop-studio tools.

All tool-handler error returns MUST go through these two helpers rather than
constructing dicts by hand. This keeps the dict shape consistent for Claude
(who relies on ``status`` / ``error_type`` / ``retry_suggested``) and gives
us a single point to evolve the shape when needed.

Canonical reason codes (by convention — not runtime-enforced):

- Terminal (``retry_suggested=False``): ``invalid_inputs``, ``invalid_workflow``,
  ``model_not_found``, ``directory_not_found``, ``permission_denied``,
  ``completed_no_output``, ``auth_failed``, ``no_credits``, ``account_issue``,
  ``rate_limited``.
- Transient (``retry_suggested=True``): ``unreachable``, ``generation_failed``,
  ``storage_error``, ``internal_error``.

The optional ``backend`` kwarg tags an error with its originating backend
(``"local"`` / ``"cloud"``). When omitted or ``None``, the returned dict
retains the original four-key shape for backwards compatibility with
untagged call sites. The ``backend`` field is omitted entirely — not
``None`` — when the caller does not provide it, so tests can use
``"backend" not in result`` as a clean absence check.

Scope boundary: not every error has a single-backend provenance. The
``safe_tool`` wrapper in ``slop_studio.server`` catches any unhandled
exception and returns an ``internal_error`` with no backend tag — the
exception could originate in any module. Router-level caller-input
validation errors that precede backend resolution also stay untagged:
unknown prompt_id prefix, mixed-backend batch, and unknown backend name.
Backend-specific modules (``backends.local``, ``backends.cloud``) tag
their own caller-input errors because they have unambiguous provenance.
"""

from dataclasses import asdict, dataclass


@dataclass
class ErrorResponse:
    status: str = "error"
    error: str = ""
    error_type: str = ""
    retry_suggested: bool = False


def transient_error(error_type: str, message: str, backend: str | None = None) -> dict:
    """Create error response for retryable failures.

    See module docstring for the optional ``backend`` tagging semantics.
    """
    response = asdict(ErrorResponse(error=message, error_type=error_type, retry_suggested=True))
    if backend is not None:
        response["backend"] = backend
    return response


def terminal_error(error_type: str, message: str, backend: str | None = None) -> dict:
    """Create error response for non-retryable failures.

    See module docstring for the optional ``backend`` tagging semantics.
    """
    response = asdict(ErrorResponse(error=message, error_type=error_type, retry_suggested=False))
    if backend is not None:
        response["backend"] = backend
    return response
