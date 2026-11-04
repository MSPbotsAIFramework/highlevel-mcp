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
