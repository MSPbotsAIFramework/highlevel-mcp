"""Opportunities / pipelines tools — read only (Product design: no writes
in v1; sales-stage changes are a later ticket)."""

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
    @log_call("highlevel_list_pipelines")
    async def highlevel_list_pipelines() -> str:
        """List the sales pipelines configured in the connected Location,
        each with its stages.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get(
                "/opportunities/pipelines", params={"locationId": client.location_id}
            )
        except HighLevelAPIError as e:
            return e.to_envelope()
        pipelines = result.get("pipelines", []) if isinstance(result, dict) else []
        return dump_json_capped({"pipelines": pipelines})

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @log_call("highlevel_list_opportunities")
    async def highlevel_list_opportunities(
        pipeline_id: Annotated[str | None, Field(description="Optional: filter to one pipeline.")] = None,
        stage_id: Annotated[str | None, Field(description="Optional: filter to one pipeline stage.")] = None,
        contact_id: Annotated[str | None, Field(description="Optional: filter to one contact's opportunities.")] = None,
        limit: Annotated[int | None, Field(description="Max results, default 50, hard cap 200.")] = None,
        cursor: Annotated[str | None, Field(description="Opaque cursor from a previous call's has_more page.")] = None,
    ) -> str:
        """List sales opportunities, optionally filtered by pipeline, stage,
        or contact.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        page_limit = clamp_limit(limit)
        cursor_params = decode_cursor(cursor)
        try:
            result = await client.get(
                "/opportunities/search",
                params={
                    "location_id": client.location_id,
                    "pipeline_id": pipeline_id,
                    "pipeline_stage_id": stage_id,
                    "contact_id": contact_id,
                    "limit": page_limit,
                    **cursor_params,
                },
            )
        except HighLevelAPIError as e:
            return e.to_envelope()
        opportunities = result.get("opportunities", []) if isinstance(result, dict) else []
        meta = result.get("meta", {}) if isinstance(result, dict) else {}
        next_cursor_fields = {
            k: meta[k] for k in ("startAfterId", "startAfter") if meta.get(k) is not None
        }
        has_more = bool(next_cursor_fields) and len(opportunities) >= page_limit
        return dump_json_capped(
            {
                "opportunities": opportunities,
                "has_more": has_more,
                "cursor": encode_cursor(next_cursor_fields) if has_more else None,
            }
        )
