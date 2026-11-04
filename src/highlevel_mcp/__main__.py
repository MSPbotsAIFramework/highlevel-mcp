import logging
import sys

from .config import Settings, get_settings
from .server import GatewayTokenMiddleware, create_mcp_server


def _build_http_app(mcp, settings: Settings):
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    mcp_app = mcp.streamable_http_app()  # Starlette app owning the session-manager lifespan
    mounted = GatewayTokenMiddleware(mcp_app, settings)

    # Mount() does NOT run a sub-app's lifespan, so the streamable-http
    # session manager's task group would never start ("Task group is not
    # initialized"). Propagate the MCP app's lifespan to the outer app
    # explicitly (SOP §7).
    return Starlette(
        routes=[Route("/health", health), Mount("/", app=mounted)],
        lifespan=lambda app: mcp_app.router.lifespan_context(app),
    )


def configure_logging() -> None:
    """Turn on the stdlib logging that tools/_common.py:log_call writes to
    (Tech design "Non-functional / Observability": tool name, outcome,
    latency per call). httpx's own request-level INFO log line prints the
    full upstream URL, which carries the Location ID in the query string
    for every GET — silence it so turning logging on cannot leak the one
    value the tech design explicitly forbids logging.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # basicConfig only sets the root level as a side effect of adding a
    # handler, which it skips if one is already attached (e.g. a test
    # runner's own log-capture handler) — set it explicitly so the level is
    # deterministic regardless of what else configured logging first.
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def main() -> None:
    configure_logging()
    settings = get_settings()
    mcp = create_mcp_server(settings)

    import uvicorn

    app = _build_http_app(mcp, settings)
    print(
        f"highlevel-mcp listening on http://{settings.mcp_http_host}:{settings.mcp_http_port}/mcp",
        file=sys.stderr,
    )
    uvicorn.run(app, host=settings.mcp_http_host, port=settings.mcp_http_port)


if __name__ == "__main__":
    main()
