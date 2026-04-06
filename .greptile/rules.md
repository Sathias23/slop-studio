## Project Overview

slop-studio is a Python MCP server that provides conversational image generation via ComfyUI.
It uses FastMCP for the server framework and httpx for async HTTP calls to ComfyUI.

## Python Standards

- Target Python 3.11+. Use modern syntax (match/case, `X | Y` unions, etc.) where appropriate.
- Follow PEP 8 conventions.
- Prefer `pathlib.Path` over `os.path` for file operations.

## Error Handling

- Use the structured error response helpers (`terminal_error`, `transient_error`) from `slop_studio/errors.py`.
- MCP tools should return user-friendly error messages, not raw tracebacks.

## Async

- The MCP server is fully async. Do not introduce synchronous blocking calls.
- Use `httpx.AsyncClient` for HTTP requests to ComfyUI and external services.

## Testing

- Tests use pytest with respx for HTTP mocking.
- Test files live in `tests/` and follow the `test_<module>.py` naming convention.
- Prefer testing behavior over implementation details.

## Security

- Never log or expose API keys, tokens, or credentials.
- Validate all user-provided file paths to prevent directory traversal.
