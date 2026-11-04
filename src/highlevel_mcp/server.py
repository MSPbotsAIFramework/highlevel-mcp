import contextvars
from collections.abc import Callable

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .client import HighLevelClient
from .config import Settings

REQUIRED_HEADERS = ["X-HighLevel-Private-Token", "X-HighLevel-Location-Id"]

# Per-request credential isolation via contextvars (SOP §3.3). Value is
# (private_token, location_id). GatewayTokenMiddleware sets this before the
# MCP handler runs and resets it in `finally`, so two concurrent requests —
# even for different Locations — never see each other's value (TC12, AC6).
_gateway_creds_var: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar(
    "highlevel_gateway_creds", default=None
)


def get_client_from_context() -> HighLevelClient | None:
    """Resolve the active HighLevelClient for the current request context."""
    creds = _gateway_creds_var.get()
    if not creds:
        return None
    token, location_id = creds
    return HighLevelClient(token, location_id)


class GatewayTokenMiddleware:
    """ASGI middleware. Reads the two required credential headers and stores
    them in the contextvar; returns 401 naming both if either is missing
    (AC2, TC2, TC3). Credentials are never read from environment variables
    or config — only from the request (SOP §3.1).
    """

    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        token = request.headers.get("x-highlevel-private-token")
        location_id = request.headers.get("x-highlevel-location-id")
        if not token or not location_id:
            response = JSONResponse(
                {
                    "error": {
                        "code": "not_configured",
                        "message": (
                            "missing required header(s): "
                            + ", ".join(REQUIRED_HEADERS)
                        ),
                        "retryable": False,
                    },
                    "required_headers": REQUIRED_HEADERS,
                },
                status_code=401,
            )
            await response(scope, receive, send)
            return

        ctx_token = _gateway_creds_var.set((token, location_id))
        try:
            await self.app(scope, receive, send)
        finally:
            _gateway_creds_var.reset(ctx_token)


def create_mcp_server(settings: Settings) -> FastMCP:
    """Build the FastMCP server instance and register all HighLevel tools."""
    # DNS-rebinding protection is a browser-oriented safeguard that rejects
    # non-localhost Host headers with 421. Disabled so the server works
    # correctly behind MSPbots's gateway / reverse proxy (SOP §7).
    mcp = FastMCP(
        name="highlevel-mcp",
        instructions=(
            "HighLevel (GoHighLevel) is a CRM and marketing-automation "
            "platform used by agencies to run their clients' sales and "
            "marketing. This server gives read access to a connected "
            "sub-account's (Location's) contacts, sales pipelines/"
            "opportunities, calendar appointments, and conversations, plus "
            "narrow, auditable write access to contacts (create, update, "
            "add a note, add tags). Four tool groups: Contacts "
            "(highlevel_search_contacts/get_contact/create_contact/"
            "update_contact/add_contact_note/add_contact_tags), "
            "Opportunities (highlevel_list_pipelines/list_opportunities), "
            "Calendar (highlevel_list_calendars/list_appointments), and "
            "Conversations (highlevel_list_conversations/"
            "get_conversation_messages). Typical flow: "
            "highlevel_search_contacts to find a contact, then "
            "highlevel_get_contact for full detail, then "
            "highlevel_create_contact / highlevel_update_contact / "
            "highlevel_add_contact_note / highlevel_add_contact_tags to act "
            "on it. List tools default to 50 results per page and expose a "
            "cursor for the next page via has_more/cursor. There are no "
            "delete tools and no payments/webhook tools in this version."
        ),
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        stateless_http=True,
        json_response=True,
    )

    client_factory: Callable[[], HighLevelClient | None] = get_client_from_context

    from .tools import calendar, contacts, conversations, opportunities

    contacts.register(mcp, client_factory)
    opportunities.register(mcp, client_factory)
    calendar.register(mcp, client_factory)
    conversations.register(mcp, client_factory)

    return mcp
