from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def create_mcp(instructions: str) -> FastMCP:
    """Create and configure the FastMCP server with all tools registered.

    Centralizes MCP construction so all transports share the same tools.
    """
    mcp = FastMCP(
        name="web-browsing-mcp",
        instructions=instructions,
    )

    # Register MCP tools from routers
    from .routers import scraping, search, agent

    scraping.register_mcp_tools(mcp)
    search.register_mcp_tools(mcp)
    agent.register_mcp_tools(mcp)

    return mcp
