"""Tools owned by the procurement hat.

Moved unchanged from Agent._run_tool_call (F-DRIVER Phase A); each registers
itself with app.agents.core.tool_registry."""
from __future__ import annotations

from app.agents.core.tool_registry import ToolCall, tool


@tool("procurement_list_generator", owner="procurement")
async def handle_procurement_list_generator(call: ToolCall) -> dict:
    """The ``procurement_list_generator`` tool."""
    from app.agents.runtime import (  # noqa: F401 -- runtime helpers, imported at call time
        Any,
    )
    args = call.args
    name = call.name
    agent = call.agent
    user_message = call.user_message
    if "construction" not in agent.allowed_blocks:
        return {
            "name": name,
            "ok": False,
            "result": {
                "status": "error",
                "error": "construction container not in agent's allowed_blocks",
            },
        }
    try:
        from app.dependencies import get_block_instance
        container = get_block_instance("construction")
    except Exception as e:
        return {
            "name": name,
            "ok": False,
            "result": {"status": "error", "error": f"construction unavailable: {e}"},
        }
    params: dict[str, Any] = {}
    nested = args.get("params")
    if isinstance(nested, dict):
        params.update(nested)
    for key in (
        "quantities", "boq", "budget", "location", "project_type",
        "schedule_start_date",
    ):
        if args.get(key) is not None:
            params.setdefault(key, args.get(key))
    input_data = args.get("input")
    if not isinstance(input_data, dict):
        input_data = {}
    else:
        input_data = dict(input_data)
    input_data.setdefault(
        "message", args.get("message") or user_message or "",
    )
    try:
        result = await container.procurement_list_generator(
            input_data, params,
        )
    except Exception as e:
        return {
            "name": name,
            "ok": False,
            "result": {
                "status": "error",
                "error": f"procurement_list_generator failed: {e}",
            },
        }
    return {
        "name": "procurement_list_generator",
        "ok": isinstance(result, dict) and result.get("status") == "success",
        "result": result,
    }
