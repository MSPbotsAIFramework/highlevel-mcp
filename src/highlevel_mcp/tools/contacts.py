"""Contacts tools: read (search/get) and four narrowly-scoped writes (create,
update, add a note, add tags). No delete tool — Product design "Non-goals"
(no destructive-mutation tools in v1).
"""

from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped, error_envelope
from ..client import HighLevelClient, HighLevelAPIError, decode_cursor, encode_cursor
from ._common import NO_TOKEN, clamp_limit, log_call


def register(mcp: FastMCP, client_factory: Callable[[], HighLevelClient | None]) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @log_call("highlevel_search_contacts")
    async def highlevel_search_contacts(
        query: Annotated[
            str | None, Field(description="Optional free-text search (name, email, phone).")
        ] = None,
        tag: Annotated[str | None, Field(description="Optional: only contacts with this tag.")] = None,
        limit: Annotated[
            int | None, Field(description="Max results, default 50, hard cap 200.")
        ] = None,
        cursor: Annotated[
            str | None, Field(description="Opaque cursor from a previous call's has_more page.")
        ] = None,
    ) -> str:
        """Search contacts in the connected HighLevel sub-account (Location).
        Returns an empty list (not an error) when nothing matches (AC3).
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        page_limit = clamp_limit(limit)
        cursor_params = decode_cursor(cursor)
        try:
            result = await client.get(
                "/contacts/",
                params={
                    "locationId": client.location_id,
                    "query": query,
                    "tag": tag,
                    "limit": page_limit,
                    **cursor_params,
                },
            )
        except HighLevelAPIError as e:
            return e.to_envelope()
        contacts = result.get("contacts", []) if isinstance(result, dict) else []
        meta = result.get("meta", {}) if isinstance(result, dict) else {}
        next_cursor_fields = {
            k: meta[k] for k in ("startAfterId", "startAfter") if meta.get(k) is not None
        }
        has_more = bool(next_cursor_fields) and len(contacts) >= page_limit
        return dump_json_capped(
            {
                "contacts": contacts,
                "has_more": has_more,
                "cursor": encode_cursor(next_cursor_fields) if has_more else None,
            }
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @log_call("highlevel_get_contact")
    async def highlevel_get_contact(
        contact_id: Annotated[str, Field(description="Required HighLevel contact ID.")],
    ) -> str:
        """Get a single contact's full detail. Returns the not_found error
        envelope (not an exception) if the ID does not exist (AC4).
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get(f"/contacts/{contact_id}")
        except HighLevelAPIError as e:
            return e.to_envelope()
        contact = result.get("contact", result) if isinstance(result, dict) else result
        return dump_json_capped({"contact": contact})

    @mcp.tool()
    @log_call("highlevel_create_contact")
    async def highlevel_create_contact(
        name: Annotated[str | None, Field(description="Full name. Optional if email or phone is given.")] = None,
        email: Annotated[str | None, Field(description="Optional, but name/email/phone — at least one is required.")] = None,
        phone: Annotated[str | None, Field(description="Optional, E.164 recommended.")] = None,
        tags: Annotated[list[str] | None, Field(description="Optional tags to apply on creation.")] = None,
    ) -> str:
        """Create a new contact in the connected Location. At least one of
        name, email, or phone is required. Returns the new contact_id
        (AC7).
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        if not name and not email and not phone:
            return error_envelope(
                "invalid_argument", "at least one of name, email, phone is required", False
            )
        body: dict = {"locationId": client.location_id}
        if name is not None:
            body["name"] = name
        if email is not None:
            body["email"] = email
        if phone is not None:
            body["phone"] = phone
        if tags is not None:
            body["tags"] = tags
        try:
            result = await client.post("/contacts/", json_body=body)
        except HighLevelAPIError as e:
            return e.to_envelope()
        contact = result.get("contact", result) if isinstance(result, dict) else result
        contact_id = contact.get("id") if isinstance(contact, dict) else None
        return dump_json_capped({"contact_id": contact_id})

    @mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
    @log_call("highlevel_update_contact")
    async def highlevel_update_contact(
        contact_id: Annotated[str, Field(description="Required HighLevel contact ID.")],
        name: Annotated[str | None, Field(description="Optional new full name.")] = None,
        email: Annotated[str | None, Field(description="Optional new email.")] = None,
        phone: Annotated[str | None, Field(description="Optional new phone.")] = None,
    ) -> str:
        """Update one or more fields on an existing contact (AC10). Only the
        fields passed are changed.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        body: dict = {}
        if name is not None:
            body["name"] = name
        if email is not None:
            body["email"] = email
        if phone is not None:
            body["phone"] = phone
        if not body:
            return error_envelope("invalid_argument", "no fields to update were given", False)
        try:
            result = await client.put(f"/contacts/{contact_id}", json_body=body)
        except HighLevelAPIError as e:
            return e.to_envelope()
        contact = result.get("contact", result) if isinstance(result, dict) else result
        return dump_json_capped({"contact": contact})

    @mcp.tool()
    @log_call("highlevel_add_contact_note")
    async def highlevel_add_contact_note(
        contact_id: Annotated[str, Field(description="Required HighLevel contact ID.")],
        note_text: Annotated[str, Field(description="Required note body.")],
    ) -> str:
        """Add a note to a contact (AC7 — visible on the next
        highlevel_get_contact call).
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.post(
                f"/contacts/{contact_id}/notes", json_body={"body": note_text}
            )
        except HighLevelAPIError as e:
            return e.to_envelope()
        note = result.get("note", result) if isinstance(result, dict) else result
        note_id = note.get("id") if isinstance(note, dict) else None
        return dump_json_capped({"note_id": note_id})

    @mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
    @log_call("highlevel_add_contact_tags")
    async def highlevel_add_contact_tags(
        contact_id: Annotated[str, Field(description="Required HighLevel contact ID.")],
        tags: Annotated[list[str], Field(description="Required list of tags to add.")],
    ) -> str:
        """Add one or more tags to a contact (additive — existing tags are
        kept). See also highlevel_update_contact for other field changes
        (AC10).
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        if not tags:
            return error_envelope("invalid_argument", "tags must be a non-empty list", False)
        try:
            result = await client.post(f"/contacts/{contact_id}/tags", json_body={"tags": tags})
        except HighLevelAPIError as e:
            return e.to_envelope()
        returned_tags = result.get("tags", tags) if isinstance(result, dict) else tags
        return dump_json_capped({"contact_id": contact_id, "tags": returned_tags})
