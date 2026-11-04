import functools
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from .._json import error_envelope

NO_TOKEN = error_envelope(
    "not_configured",
    "No HighLevel credentials. Send the X-HighLevel-Private-Token and "
    "X-HighLevel-Location-Id headers.",
    False,
)

# Tech design "uniform pagination contract": every list tool uses the same
# limit/cursor shape regardless of HighLevel's native pagination mechanism.
DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_PAGE_LIMIT
    if limit <= 0:
        return DEFAULT_PAGE_LIMIT
    return min(limit, MAX_PAGE_LIMIT)


_logger = logging.getLogger("highlevel_mcp.tools")

_F = TypeVar("_F", bound=Callable[..., Awaitable[str]])


def _outcome_of(result: str) -> str:
    """Return "success", or the error envelope's `code` — read only from
    the small structured shape, never the full body. Tech design
    "Non-functional / Observability" forbids logging full response
    bodies (may contain PII such as contact emails/phones).
    """
    try:
        parsed: Any = json.loads(result)
    except (TypeError, ValueError):
        return "success"
    if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
        return str(parsed["error"].get("code", "error"))
    return "success"


def log_call(tool_name: str) -> Callable[[_F], _F]:
    """Decorator for a tool's implementation function. Logs tool name,
    outcome (success/error code), and latency per call, per Tech design
    "Non-functional / Observability". Never logs the credential header
    values or the response body itself — only the summary line below.
    """

    def decorator(fn: _F) -> _F:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> str:
            start = time.monotonic()
            try:
                result = await fn(*args, **kwargs)
            except Exception:
                latency_ms = round((time.monotonic() - start) * 1000, 1)
                _logger.info(
                    "tool=%s outcome=exception latency_ms=%s", tool_name, latency_ms
                )
                raise
            latency_ms = round((time.monotonic() - start) * 1000, 1)
            _logger.info(
                "tool=%s outcome=%s latency_ms=%s",
                tool_name,
                _outcome_of(result),
                latency_ms,
            )
            return result

        return wrapper  # type: ignore[return-value]

    return decorator
