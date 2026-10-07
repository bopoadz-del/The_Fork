"""A driver turn: the model calls tools until it answers, within the
configured number of steps. Events have the shapes the chat stream already
uses (status, tool_call, tool_result, token, end)."""
from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from app.agents import driver
from app.agents.driver import context, llm, routes, tools
from app.core import turn_progress

_LOG = logging.getLogger(__name__)

_STOP = ("I could not finish this answer within the steps a turn may take. "
         "Please narrow the question or split it into parts.")


def _args(tool_call: Dict[str, Any]) -> Dict[str, Any]:
    raw = (tool_call.get("function") or {}).get("arguments") or "{}"
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        _LOG.warning("driver: tool %r arguments are not JSON; calling it with none",
                     (tool_call.get("function") or {}).get("name"))
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


async def stream(agent: Any, user_message: str, history: Optional[list] = None,
                 project_id: Optional[str] = None, conversation_id: Optional[str] = None,
                 user_id: Optional[str] = None, api_key: Optional[str] = None,
                 **_ignored: Any) -> AsyncIterator[Dict[str, Any]]:
    t0 = time.monotonic()
    disciplines = [h["discipline"] for h in context.hats()]
    hat: Optional[str] = None
    msgs: List[Dict[str, Any]] = context.messages(user_message, history, project_id, user_id, hat)
    used: List[str] = []
    exports: List[Dict[str, Any]] = []
    yield {"type": "start", "mode": "driver", "agent": agent.name}
    yield turn_progress.event("writing")
    answer: Optional[str] = None
    for step in range(driver.max_steps()):
        offered = tools.offered(agent, project_id, hat, disciplines)
        reply = await llm.call(agent, msgs, offered, model=driver.model())
        if reply["status"] != "success":
            yield {"type": "error", "message": reply["error"]}
            return
        message = reply["message"]
        calls = message.get("tool_calls") or []
        if not calls:
            answer = (message.get("content") or "").strip()
            break
        msgs.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
        for call in calls:
            name = (call.get("function") or {}).get("name") or ""
            args = _args(call)
            yield {"type": "tool_call", "tool": name, "name": name, "args_preview": json.dumps(args)[:200]}
            if name == tools.SELECT_HAT:
                chosen = str(args.get("hat") or "")
                if chosen in disciplines:
                    hat = chosen
                    msgs[0] = context.system_message(project_id, user_id, hat)
                    result: Dict[str, Any] = {"ok": True, "hat": hat}
                else:
                    result = {"ok": False, "error": f"No hat named {chosen!r}; choose one of {sorted(disciplines)}."}
            elif name == routes.RUN_WORKFLOW:
                used.append(f"{name}:{args.get('workflow')}")
                result = await routes.run(args, user_message, project_id, user_id, conversation_id)
                exports.extend(result.get("exports") or [])
            else:
                used.append(name)
                result = await agent._run_tool_call(call, api_key, project_id, conversation_id,
                                                    user_message=user_message, history=history)
            yield {"type": "tool_result", "tool": name, "name": name, "ok": bool(result.get("ok", True))}
            msgs.append({"role": "tool", "tool_call_id": call.get("id") or name,
                         "content": json.dumps(result, default=str)[:20000]})
    if answer is None:
        answer = _STOP
    for word in answer.split(" "):
        yield {"type": "token", "content": word + " "}
    yield {"type": "end", "complete": True, "mode": "driver", "hat": hat, "tools": used, "exports": exports,
           "steps": step + 1, "elapsed_s": round(time.monotonic() - t0, 2)}
