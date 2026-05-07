"""MCP adapter — expose every Cerebrum block as a tool to MCP-aware agents.

Two ways to use this:

1. Programmatic: `MCPAdapterBlock` is a UniversalBlock — `execute({"action": "list_tools"})`
   returns the synthesized tool catalog. Useful for debugging.

2. Server: the SSE endpoint `/mcp/sse` (see `app/routers/mcp.py`) wires this same
   catalog into an `mcp.server.Server` instance and serves it over Server-Sent
   Events so Claude Desktop / Copilot / any MCP client can discover the tools.
"""

from typing import Any, Dict

from app.core.universal_base import UniversalBlock


class MCPAdapterBlock(UniversalBlock):
    name = "mcp_adapter"
    version = "1.0.0"
    description = "Expose Cerebrum blocks to MCP-aware agents (Claude, Copilot, Kimi, ...)"
    layer = 0
    tags = ["mcp", "agents", "infrastructure", "interop"]

    ui_schema = {
        "input": {"type": "json", "placeholder": '{"action": "list_tools"}'},
        "output": {"type": "json", "fields": [{"name": "tools", "type": "json", "label": "Tool catalog"}]},
        "quick_actions": [
            {"icon": "🛠", "label": "List exposed tools", "prompt": '{"action": "list_tools"}'},
        ],
    }

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        action = (params.get("action") or (input_data or {}).get("action") if isinstance(input_data, dict) else "list_tools") or "list_tools"

        if action == "list_tools":
            return {"status": "success", "tools": self._build_tool_catalog()}

        if action == "describe":
            tool = params.get("tool") or (input_data or {}).get("tool")
            cat = {t["name"]: t for t in self._build_tool_catalog()}
            entry = cat.get(tool)
            if not entry:
                return {"status": "error", "error": f"Unknown tool: {tool}"}
            return {"status": "success", "tool": entry}

        return {"status": "error", "error": f"Unknown action: {action}"}

    @staticmethod
    def _build_tool_catalog() -> list:
        """Synthesize an MCP-style tool description from every registered block.

        We deliberately import lazily to avoid circular imports at module load.
        """
        from app.blocks import BLOCK_REGISTRY  # noqa: WPS433

        tools = []
        for name, block_class in BLOCK_REGISTRY.items():
            if name == "mcp_adapter":  # don't expose ourselves
                continue
            description = getattr(block_class, "description", "") or f"Block: {name}"
            ui = getattr(block_class, "ui_schema", {}) or {}
            tools.append({
                "name": name,
                "description": description,
                # MCP wants JSON Schema; we keep it loose so anything goes through.
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "input": {"description": "Block input — string, dict, or chain output."},
                        "params": {"type": "object", "description": "Optional block-specific parameters."},
                    },
                },
                "ui_hint": ui,
            })
        return tools
