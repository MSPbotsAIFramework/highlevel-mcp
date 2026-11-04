"""tools/list snapshot + per-tool unit tests, driven in-process through
FastMCP's real dispatch path (mcp.call_tool), with HighLevelClient's HTTP
methods monkeypatched — no real network calls. Per-request credentials are
supplied through the same _gateway_creds_var contextvar test_middleware.py
exercises.
"""

import json

import pytest

from highlevel_mcp.client import HighLevelAPIError, HighLevelClient, encode_cursor
from highlevel_mcp.config import Settings
from highlevel_mcp.server import _gateway_creds_var, create_mcp_server

# Required params + annotation hints for each of the 12 tools (Tech design
# §4 Data & API contract). Any signature change here is a breaking change
# (SOP §13) and must be reflected in README's tool table too.
EXPECTED_TOOLS = {
    "highlevel_search_contacts": (set(), {"readOnlyHint": True}),
    "highlevel_get_contact": ({"contact_id"}, {"readOnlyHint": True}),
    "highlevel_create_contact": (set(), {}),
    "highlevel_update_contact": ({"contact_id"}, {"idempotentHint": True}),
    "highlevel_add_contact_note": ({"contact_id", "note_text"}, {}),
    "highlevel_add_contact_tags": ({"contact_id", "tags"}, {"idempotentHint": True}),
    "highlevel_list_pipelines": (set(), {"readOnlyHint": True}),
    "highlevel_list_opportunities": (set(), {"readOnlyHint": True}),
    "highlevel_list_calendars": (set(), {"readOnlyHint": True}),
    "highlevel_list_appointments": ({"start_date", "end_date"}, {"readOnlyHint": True}),
    "highlevel_list_conversations": (set(), {"readOnlyHint": True}),
    "highlevel_get_conversation_messages": ({"conversation_id"}, {"readOnlyHint": True}),
}


async def _call(monkeypatch, method: str, fake, name: str, arguments: dict, location_id="loc-A"):
    """Drive a tool through the real FastMCP dispatch path with
    HighLevelClient.<method> monkeypatched. Returns the decoded JSON payload.
    """
    monkeypatch.setattr(HighLevelClient, method, fake)
    mcp = create_mcp_server(Settings())
    token = _gateway_creds_var.set(("tok", location_id))
    try:
        _, structured = await mcp.call_tool(name, arguments)
    finally:
        _gateway_creds_var.reset(token)
    return json.loads(structured["result"])


@pytest.mark.asyncio
async def test_tools_list_snapshot():
    """TC1 / AC1."""
    mcp = create_mcp_server(Settings())
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == set(EXPECTED_TOOLS), f"unexpected tool set: {names}"
    assert len(tools) == 12

    by_name = {t.name: t for t in tools}
    for name, (expected_required, expected_annotations) in EXPECTED_TOOLS.items():
        tool = by_name[name]
        required = set(tool.inputSchema.get("required", []))
        assert required == expected_required, f"{name}: required={required}"

        ann = tool.annotations
        for hint, value in expected_annotations.items():
            actual = getattr(ann, hint, None) if ann is not None else None
            assert actual == value, f"{name}: {hint}={actual}, expected {value}"

        first_line = (tool.description or "").strip().splitlines()[0]
        assert len(first_line) <= 100, f"{name}: first line too long: {first_line!r}"
        assert len(tool.description or "") <= 500, f"{name}: description too long"


@pytest.mark.asyncio
async def test_service_instructions_present_and_bounded():
    mcp = create_mcp_server(Settings())
    assert mcp.instructions
    assert len(mcp.instructions) <= 1500


@pytest.mark.asyncio
async def test_search_contacts_empty_result(monkeypatch):
    """TC4 / AC3 — stubbed-upstream unit test; needs a real vendor account
    for an end-to-end confirmation.
    """

    async def fake_get(self, path, params=None):
        assert path == "/contacts/"
        return {"contacts": [], "meta": {}}

    payload = await _call(
        monkeypatch, "get", fake_get, "highlevel_search_contacts", {"query": "no-such-person"}
    )
    assert payload == {"contacts": [], "has_more": False, "cursor": None}


@pytest.mark.asyncio
async def test_get_contact_not_found(monkeypatch):
    """TC5 / AC4 — stubbed-upstream unit test; needs a real vendor account
    for an end-to-end confirmation.
    """

    async def fake_get(self, path, params=None):
        raise HighLevelAPIError(404, "contact not found")

    payload = await _call(
        monkeypatch, "get", fake_get, "highlevel_get_contact", {"contact_id": "missing-id"}
    )
    assert payload == {"error": {"code": "not_found", "message": "contact not found", "retryable": False}}


@pytest.mark.asyncio
async def test_rate_limited_maps_to_error_envelope(monkeypatch):
    """TC6 / AC5 (tool layer) — a persistent 429 surfaces through the tool
    as the rate_limited error envelope, not a raw passthrough of the
    vendor's response shape. The retry-with-backoff behavior itself lives
    inside client.py's _request and is covered end to end by
    test_client_retries_on_429_then_raises_rate_limited below.
    """

    async def flaky_get(self, path, params=None):
        raise HighLevelAPIError(429, "rate limited by HighLevel")

    payload = await _call(monkeypatch, "get", flaky_get, "highlevel_search_contacts", {})
    assert payload["error"]["code"] == "rate_limited"
    assert payload["error"]["retryable"] is True


@pytest.mark.asyncio
async def test_client_retries_on_429_then_raises_rate_limited(monkeypatch):
    """Exercises HighLevelClient._request's real retry loop (not stubbed at
    the .get() boundary) using a fake httpx client, proving a 429 triggers
    at least one retry with backoff before the rate_limited error surfaces.
    """
    import highlevel_mcp.client as client_module

    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    class FakeResponse:
        status_code = 429
        headers: dict = {}

        def json(self):
            return {"message": "rate limited"}

    class FakeHttpClient:
        def __init__(self):
            self.calls = 0

        async def request(self, method, url, headers=None, params=None, json=None):
            self.calls += 1
            return FakeResponse()

    fake_client = FakeHttpClient()
    monkeypatch.setattr(client_module, "_get_http_client", lambda: fake_client)

    hl_client = client_module.HighLevelClient("tok", "loc-A")
    with pytest.raises(client_module.HighLevelAPIError) as exc_info:
        await hl_client.get("/contacts/", params={"locationId": "loc-A"})

    assert exc_info.value.status_code == 429
    envelope = json.loads(exc_info.value.to_envelope())
    assert envelope["error"]["code"] == "rate_limited"
    assert envelope["error"]["retryable"] is True
    # 4 total attempts (1 + _MAX_RETRIES=3), so 3 backoff sleeps happened.
    assert fake_client.calls == 4
    assert len(sleeps) == 3


@pytest.mark.asyncio
async def test_list_appointments_empty_range(monkeypatch):
    """TC9 / AC8 — stubbed-upstream unit test; needs a real vendor account
    for an end-to-end confirmation.
    """

    async def fake_get(self, path, params=None):
        assert path == "/calendars/events"
        return {"events": [], "meta": {}}

    payload = await _call(
        monkeypatch,
        "get",
        fake_get,
        "highlevel_list_appointments",
        {"start_date": "2026-11-10", "end_date": "2026-11-11"},
    )
    assert payload == {"appointments": [], "has_more": False, "cursor": None}


@pytest.mark.asyncio
async def test_search_contacts_pagination_caps_at_200(monkeypatch):
    """TC10 / AC9 — a result set larger than 50 items, called without
    `limit`, returns exactly 50 items and has_more: true with a non-empty
    cursor; a call requesting limit=10000 is capped at 200 items.
    """
    captured_params: dict = {}

    def _make_contacts(n):
        return [{"id": f"c{i}"} for i in range(n)]

    async def fake_get_default(self, path, params=None):
        captured_params.update(params or {})
        limit = params["limit"]
        return {
            "contacts": _make_contacts(limit),
            "meta": {"startAfterId": "next-id", "startAfter": 12345},
        }

    payload = await _call(monkeypatch, "get", fake_get_default, "highlevel_search_contacts", {})
    assert captured_params["limit"] == 50
    assert len(payload["contacts"]) == 50
    assert payload["has_more"] is True
    assert payload["cursor"]

    captured_params.clear()

    async def fake_get_capped(self, path, params=None):
        captured_params.update(params or {})
        limit = params["limit"]
        return {"contacts": _make_contacts(limit), "meta": {}}

    payload = await _call(
        monkeypatch, "get", fake_get_capped, "highlevel_search_contacts", {"limit": 10000}
    )
    assert captured_params["limit"] == 200
    assert len(payload["contacts"]) == 200


@pytest.mark.asyncio
async def test_search_contacts_cursor_roundtrip(monkeypatch):
    """A cursor returned by one call is decoded back into the upstream
    pagination fields on the next call — the tool contract never leaks
    HighLevel's native field names to the caller.
    """
    seen_params: list[dict] = []

    async def fake_get(self, path, params=None):
        seen_params.append(dict(params or {}))
        return {"contacts": [], "meta": {}}

    cursor = encode_cursor({"startAfterId": "abc", "startAfter": 999})
    await _call(monkeypatch, "get", fake_get, "highlevel_search_contacts", {"cursor": cursor})
    assert seen_params[0]["startAfterId"] == "abc"
    assert seen_params[0]["startAfter"] == 999


@pytest.mark.asyncio
async def test_create_contact_requires_an_identifier():
    # highlevel_create_contact must reject an empty call before ever
    # touching the HTTP client (no identifier given).
    mcp = create_mcp_server(Settings())
    token = _gateway_creds_var.set(("tok", "loc-A"))
    try:
        _, structured = await mcp.call_tool("highlevel_create_contact", {})
    finally:
        _gateway_creds_var.reset(token)
    payload = json.loads(structured["result"])
    assert payload["error"]["code"] == "invalid_argument"


@pytest.mark.asyncio
async def test_create_contact_returns_new_contact_id(monkeypatch):
    """Half of TC8 / AC7 (the create half; the note half is
    test_add_contact_note_returns_note_id below) — stubbed-upstream unit
    test; needs a real vendor account for the full round-trip.
    """

    async def fake_post(self, path, json_body=None):
        assert path == "/contacts/"
        assert json_body["locationId"] == "loc-A"
        return {"contact": {"id": "new-contact-1"}}

    payload = await _call(
        monkeypatch,
        "post",
        fake_post,
        "highlevel_create_contact",
        {"name": "Jane Doe", "email": "jane@example.com"},
    )
    assert payload == {"contact_id": "new-contact-1"}


@pytest.mark.asyncio
async def test_add_contact_note_returns_note_id(monkeypatch):
    """Other half of TC8 / AC7."""

    async def fake_post(self, path, json_body=None):
        assert path == "/contacts/new-contact-1/notes"
        assert json_body == {"body": "called, interested"}
        return {"note": {"id": "note-1"}}

    payload = await _call(
        monkeypatch,
        "post",
        fake_post,
        "highlevel_add_contact_note",
        {"contact_id": "new-contact-1", "note_text": "called, interested"},
    )
    assert payload == {"note_id": "note-1"}


@pytest.mark.asyncio
async def test_update_contact_and_add_tags_roundtrip(monkeypatch):
    """TC11 / AC10 — stubbed-upstream unit test; needs a real vendor account
    for the full round-trip against highlevel_get_contact.
    """

    async def fake_put(self, path, json_body=None):
        assert path == "/contacts/c1"
        assert json_body == {"phone": "+15551234567"}
        return {"contact": {"id": "c1", "phone": "+15551234567"}}

    payload = await _call(
        monkeypatch,
        "put",
        fake_put,
        "highlevel_update_contact",
        {"contact_id": "c1", "phone": "+15551234567"},
    )
    assert payload["contact"]["phone"] == "+15551234567"

    async def fake_post(self, path, json_body=None):
        assert path == "/contacts/c1/tags"
        assert json_body == {"tags": ["vip"]}
        return {"tags": ["vip"]}

    payload = await _call(
        monkeypatch, "post", fake_post, "highlevel_add_contact_tags", {"contact_id": "c1", "tags": ["vip"]}
    )
    assert payload == {"contact_id": "c1", "tags": ["vip"]}


@pytest.mark.asyncio
async def test_no_delete_tools_exist():
    """Product design Non-goal: no delete tools of any kind."""
    mcp = create_mcp_server(Settings())
    tools = await mcp.list_tools()
    for tool in tools:
        assert "delete" not in tool.name
        ann = tool.annotations
        assert not (ann is not None and ann.destructiveHint)
