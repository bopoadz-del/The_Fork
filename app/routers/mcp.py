"""MCP server-sent-events endpoint at /mcp/sse.

Lets MCP-aware clients (Claude Desktop, MCP-compatible IDEs) connect to this
FastAPI app and discover every block in BLOCK_REGISTRY as a callable tool.

This module is import-safe even if the optional `mcp` package is not installed
or doesn't ship the SSE transport — see `mcp_router_available()`.
"""

from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


def mcp_router_available() -> bool:
    try:
        from mcp.server import Server  # noqa: F401
        from mcp.server.sse import SseServerTransport  # noqa: F401
        return True
    except Exception:
        return False


@router.get("/mcp/info")
async def mcp_info():
    """Lightweight JSON describing the MCP surface — works even without SSE deps."""
    from app.blocks import BLOCK_REGISTRY

    return {
        "available": mcp_router_available(),
        "tool_count": len(BLOCK_REGISTRY),
        "tools": sorted(BLOCK_REGISTRY.keys()),
        "endpoints": {
            "sse": "/mcp/sse" if mcp_router_available() else None,
            "info": "/mcp/info",
        },
    }


if mcp_router_available():
    from mcp.server import Server
    from mcp.server.sse import SseServerTransport
    from mcp.types import TextContent, Tool

    _sse = SseServerTransport("/mcp/messages")

    def _build_server() -> "Server":
        from app.blocks import BLOCK_REGISTRY
        from app.dependencies import block_instances, _create_block_instance

        server = Server("cerebrum-blocks")

        @server.list_tools()
        async def _list_tools():
            tools = []
            for name, block_class in BLOCK_REGISTRY.items():
                if name in ("mcp_adapter",):
                    continue
                tools.append(
                    Tool(
                        name=name,
                        description=getattr(block_class, "description", "") or f"Block: {name}",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "input": {"description": "Block input"},
                                "params": {"type": "object", "description": "Optional block params"},
                            },
                        },
                    )
                )
            return tools

        @server.call_tool()
        async def _call_tool(name: str, arguments: dict):
            if name not in BLOCK_REGISTRY:
                return [TextContent(type="text", text=f"Unknown tool: {name}")]
            instance = block_instances.get(name) or _create_block_instance(name)
            block_input = (arguments or {}).get("input")
            block_params = (arguments or {}).get("params") or {}
            result = await instance.execute(block_input, block_params)
            import json
            return [TextContent(type="text", text=json.dumps(result, default=str))]

        return server

    @router.get("/mcp/sse")
    async def mcp_sse(request: Request):
        async with _sse.connect_sse(request.scope, request.receive, request._send) as streams:
            server = _build_server()
            await server.run(streams[0], streams[1], server.create_initialization_options())

else:

    @router.get("/mcp/sse")
    async def mcp_sse_unavailable():
        return JSONResponse(
            status_code=503,
            content={"detail": "MCP SSE transport not available — install 'mcp[server]'."},
        )
