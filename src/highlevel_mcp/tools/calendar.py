"""Calendar / appointments tools — read only."""

from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped
from ..client import HighLevelClient, HighLevelAPIError, decode_cursor, encode_cursor
from ._common import NO_TOKEN, clamp_limit, log_call


def register(mcp: FastMCP, client_factory: Callable[[], HighLevelClient | None]) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @log_call("highlevel_list_calendars")
    async def highlevel_list_calendars() -> str:
        """List the calendars configured in the connected Location."""
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get("/calendars/", params={"locationId": client.location_id})
        except HighLevelAPIError as e:
            return e.to_envelope()
        calendars = result.get("calendars", []) if isinstance(result, dict) else []
        return dump_json_capped({"calendars": calendars})

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @log_call("highlevel_list_appointments")
    async def highlevel_list_appointments(
        start_date: Annotated[str, Field(description="Required range start, ISO 8601 date or datetime.")],
        end_date: Annotated[str, Field(description="Required range end, ISO 8601 date or datetime.")],
        calendar_id: Annotated[str | None, Field(description="Optional: filter to one calendar.")] = None,
        limit: Annotated[int | None, Field(description="Max results, default 50, hard cap 200.")] = None,
        cursor: Annotated[str | None, Field(description="Opaque cursor from a previous call's has_more page.")] = None,
    ) -> str:
        """List calendar appointments/events in a date range. Returns an
        empty list (not an error) for a range with no events (AC8).
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        page_limit = clamp_limit(limit)
        cursor_params = decode_cursor(cursor)
        try:
            result = await client.get(
                "/calendars/events",
                params={
                    "locationId": client.location_id,
                    "calendarId": calendar_id,
                    "startTime": start_date,
                    "endTime": end_date,
                    "limit": page_limit,
                    **cursor_params,
                },
            )
        except HighLevelAPIError as e:
            return e.to_envelope()
        appointments = result.get("events", result.get("appointments", [])) if isinstance(result, dict) else []
        meta = result.get("meta", {}) if isinstance(result, dict) else {}
        next_cursor_fields = {
            k: meta[k] for k in ("startAfterId", "startAfter") if meta.get(k) is not None
        }
        has_more = bool(next_cursor_fields) and len(appointments) >= page_limit
        return dump_json_capped(
            {
                "appointments": appointments,
                "has_more": has_more,
                "cursor": encode_cursor(next_cursor_fields) if has_more else None,
            }
        )
