from dataclasses import dataclass, asdict


@dataclass
class ErrorResponse:
    status: str = "error"
    error: str = ""
    error_type: str = ""
    retry_suggested: bool = False


def transient_error(error_type: str, message: str) -> dict:
    """Create error response for retryable failures."""
    return asdict(ErrorResponse(error=message, error_type=error_type, retry_suggested=True))


def terminal_error(error_type: str, message: str) -> dict:
    """Create error response for non-retryable failures."""
    return asdict(ErrorResponse(error=message, error_type=error_type, retry_suggested=False))
