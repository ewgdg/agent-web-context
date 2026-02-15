from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette


def create_mcp(instructions: str) -> FastMCP:
    """Create and configure the FastMCP server with all tools registered.

    Centralizes MCP construction so all transports share the same tools.
    """
    allowed_hosts_raw = os.getenv("MCP_ALLOWED_HOSTS", "").strip()
    allowed_origins_raw = os.getenv("MCP_ALLOWED_ORIGINS", "").strip()

    # Default to *disabled* DNS rebinding protection so the service can run locally
    # (or behind a trusted reverse proxy) without managing allow-lists.
    #
    # If MCP_ALLOWED_HOSTS / MCP_ALLOWED_ORIGINS are provided, we enable protection
    # and enforce those allow-lists.
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    )
    if allowed_hosts_raw or allowed_origins_raw:
        allowed_hosts = [v.strip() for v in allowed_hosts_raw.split(",") if v.strip()]
        allowed_origins = [
            v.strip() for v in allowed_origins_raw.split(",") if v.strip()
        ]
        transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
        )

    mcp = FastMCP(
        name="web-browsing-mcp",
        instructions=instructions,
        # Expose MCP endpoints directly (we mount the combined MCP ASGI app at `/`
        # in FastAPI, after all other routes, so there are no collisions).
        streamable_http_path="/mcp",
        sse_path="/mcp/sse",
        message_path="/mcp/messages/",
        transport_security=transport_security,
    )

    # Register MCP tools from routers
    from .routers import scraping, search, agent

    scraping.register_mcp_tools(mcp)
    search.register_mcp_tools(mcp)
    agent.register_mcp_tools(mcp)

    return mcp


def create_mcp_asgi_app(mcp: FastMCP) -> Starlette:
    """Create a single ASGI app exposing both MCP transports.

    With the default settings in `create_mcp()`, the endpoints are:
    - Streamable HTTP: `/mcp`
    - SSE: `/mcp/sse` and `/mcp/messages`
    """
    sse_app = mcp.sse_app()
    streamable_app = mcp.streamable_http_app()

    middleware = [*sse_app.user_middleware, *streamable_app.user_middleware]
    routes = [*sse_app.routes, *streamable_app.routes]
    lifespan = streamable_app.router.lifespan_context

    return Starlette(
        debug=mcp.settings.debug,
        routes=routes,
        middleware=middleware,
        lifespan=lifespan,
    )
