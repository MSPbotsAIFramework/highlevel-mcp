"""Gateway credential middleware tests: missing-header 401 (naming both
required headers), and header values correctly reaching the per-request
contextvar with no leakage across requests (TC2, TC3, TC12 / AC2, AC6).
"""

import asyncio

from starlette.testclient import TestClient

from highlevel_mcp.__main__ import _build_http_app
from highlevel_mcp.config import Settings
from highlevel_mcp.server import (
    GatewayTokenMiddleware,
    _gateway_creds_var,
    create_mcp_server,
    get_client_from_context,
)


def _make_app():
    settings = Settings()
    mcp = create_mcp_server(settings)
    return _build_http_app(mcp, settings), settings


def test_health_is_local_and_does_not_require_credentials():
    app, _ = _make_app()
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


def test_missing_both_headers_returns_401_naming_both():
    """TC2 / TC3 / AC2 — a tools/call with the token header missing returns
    HTTP 401 naming both required headers.
    """
    app, _ = _make_app()
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["required_headers"] == [
            "X-HighLevel-Private-Token",
            "X-HighLevel-Location-Id",
        ]
        assert body["error"]["code"] == "not_configured"


def test_missing_location_header_returns_401_naming_both():
    """TC3 / AC2 — only the token header present is still a 401 naming both
    required headers (not just "at least one of them").
    """
    app, _ = _make_app()
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={
                "Accept": "application/json, text/event-stream",
                "X-HighLevel-Private-Token": "tok",
                # X-HighLevel-Location-Id intentionally omitted
            },
        )
        assert resp.status_code == 401
        body = resp.json()
        assert "X-HighLevel-Location-Id" in body["required_headers"]


def test_missing_token_header_returns_401_naming_both():
    """TC2 / AC2 — only the location header present is still a 401."""
    app, _ = _make_app()
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={
                "Accept": "application/json, text/event-stream",
                "X-HighLevel-Location-Id": "loc-A",
                # X-HighLevel-Private-Token intentionally omitted
            },
        )
        assert resp.status_code == 401
        body = resp.json()
        assert "X-HighLevel-Private-Token" in body["required_headers"]


def test_both_headers_present_reaches_request_context():
    """Confirms the header values that arrive on the request are exactly
    what get_client_from_context sees, and that they are reset afterward
    (no leakage to the next request).
    """
    settings = Settings()
    seen = {}

    async def fake_app(scope, receive, send):
        seen["creds"] = _gateway_creds_var.get()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = GatewayTokenMiddleware(fake_app, settings)

    async def run():
        scope = {
            "type": "http",
            "path": "/mcp",
            "headers": [
                (b"x-highlevel-private-token", b"test-token-123"),
                (b"x-highlevel-location-id", b"loc-A"),
            ],
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            pass

        await middleware(scope, receive, send)

    asyncio.run(run())
    assert seen["creds"] == ("test-token-123", "loc-A")
    # After the request completes, the contextvar must be reset — a fresh
    # get() outside any request context sees no leftover credential.
    assert _gateway_creds_var.get() is None


def test_client_factory_returns_none_without_context():
    assert get_client_from_context() is None


def _run_middleware_with(headers):
    seen = {}

    async def fake_app(scope, receive, send):
        seen["creds"] = _gateway_creds_var.get()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = GatewayTokenMiddleware(fake_app, Settings())

    async def run():
        scope = {"type": "http", "path": "/mcp", "headers": headers}

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            pass

        await middleware(scope, receive, send)

    asyncio.run(run())
    return seen["creds"]


def test_context_isolation_across_concurrent_requests():
    """TC12 — permission/multi-tenant (backend): the locationId used by a
    request's outbound call is always the one from that same request's
    context, never a value left over from a previous request's ContextVar
    (AC6's backend mechanism — two interleaved fake requests never cross-read
    each other's context).
    """

    async def run_two_interleaved():
        results = {}

        async def fake_app_a(scope, receive, send):
            # Simulate work happening between set() and read, during which
            # another "request" (task) could run on the same event loop.
            await asyncio.sleep(0.01)
            results["a"] = _gateway_creds_var.get()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        async def fake_app_b(scope, receive, send):
            results["b"] = _gateway_creds_var.get()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        mw_a = GatewayTokenMiddleware(fake_app_a, Settings())
        mw_b = GatewayTokenMiddleware(fake_app_b, Settings())

        async def noop_receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def noop_send(message):
            pass

        scope_a = {
            "type": "http",
            "path": "/mcp",
            "headers": [
                (b"x-highlevel-private-token", b"token-A"),
                (b"x-highlevel-location-id", b"location-A"),
            ],
        }
        scope_b = {
            "type": "http",
            "path": "/mcp",
            "headers": [
                (b"x-highlevel-private-token", b"token-B"),
                (b"x-highlevel-location-id", b"location-B"),
            ],
        }

        await asyncio.gather(
            mw_a(scope_a, noop_receive, noop_send),
            mw_b(scope_b, noop_receive, noop_send),
        )
        return results

    results = asyncio.run(run_two_interleaved())
    assert results["a"] == ("token-A", "location-A")
    assert results["b"] == ("token-B", "location-B")
