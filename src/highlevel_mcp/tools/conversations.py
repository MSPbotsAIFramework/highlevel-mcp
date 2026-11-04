"""Conversations tools — read only."""

from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped
from ..client import HighLevelClient, HighLevelAPIError, decode_cursor, encode_cursor
from ._common import NO_TOKEN, clamp_limit


def register(mcp: FastMCP, client_factory: Callable[[], HighLevelClient | None]) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def highlevel_list_conversations(
        contact_id: Annotated[str | None, Field(description="Optional: filter to one contact's conversations.")] = None,
        status: Annotated[str | None, Field(description="Optional: filter by conversation status.")] = None,
        limit: Annotated[int | None, Field(description="Max results, default 50, hard cap 200.")] = None,
        cursor: Annotated[str | None, Field(description="Opaque cursor from a previous call's has_more page.")] = None,
    ) -> str:
        """List conversations in the connected Location, optionally filtered
        by contact or status.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        page_limit = clamp_limit(limit)
        cursor_params = decode_cursor(cursor)
        try:
            result = await client.get(
                "/conversations/search",
                params={
                    "locationId": client.location_id,
                    "contactId": contact_id,
                    "status": status,
                    "limit": page_limit,
                    **cursor_params,
                },
            )
        except HighLevelAPIError as e:
            return e.to_envelope()
        conversations = result.get("conversations", []) if isinstance(result, dict) else []
        meta = result.get("meta", {}) if isinstance(result, dict) else {}
        next_cursor_fields = {
            k: meta[k] for k in ("startAfterId", "startAfter") if meta.get(k) is not None
        }
        has_more = bool(next_cursor_fields) and len(conversations) >= page_limit
        return dump_json_capped(
            {
                "conversations": conversations,
                "has_more": has_more,
                "cursor": encode_cursor(next_cursor_fields) if has_more else None,
            }
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def highlevel_get_conversation_messages(
        conversation_id: Annotated[str, Field(description="Required conversation ID.")],
        limit: Annotated[int | None, Field(description="Max results, default 50, hard cap 200.")] = None,
        cursor: Annotated[str | None, Field(description="Opaque cursor from a previous call's has_more page.")] = None,
    ) -> str:
        """Get the messages in a conversation, oldest-filtering handled by
        the cursor (not reversed server-side).
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        page_limit = clamp_limit(limit)
        cursor_params = decode_cursor(cursor)
        try:
            result = await client.get(
                f"/conversations/{conversation_id}/messages",
                params={"limit": page_limit, **cursor_params},
            )
        except HighLevelAPIError as e:
            return e.to_envelope()
        messages = result.get("messages", []) if isinstance(result, dict) else []
        meta = result.get("meta", {}) if isinstance(result, dict) else {}
        next_cursor_fields = {
            k: meta[k] for k in ("startAfterId", "startAfter") if meta.get(k) is not None
        }
        has_more = bool(next_cursor_fields) and len(messages) >= page_limit
        return dump_json_capped(
            {
                "messages": messages,
                "has_more": has_more,
                "cursor": encode_cursor(next_cursor_fields) if has_more else None,
            }
        )
