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

#: How much of one tool result the model is given.
_TOOL_RESULT_CHARS = 20000

_STOP = ("I could not finish this answer within the steps a turn may take. "
         "Please narrow the question or split it into parts.")


def exit_check(answer: str, msgs: List[Dict[str, Any]]) -> tuple:
    """The one check before a driver answer leaves: an attribution no
    evidence record backs is removed (citation_provenance.gate), and every
    figure is credited to the user's words, a tool run or a retrieved
    passage -- one that matches none is removed (figure_provenance). The
    evidence is this turn's own messages: the user's words and the tool
    runs. Returns (answer, provenance trail, report): the report names the
    figures removed and counts the passages read, for the end event."""
    from app.agents import citation_provenance as cp

    checked = cp.gate(answer, None, msgs, tool_passages=True)
    _, every = cp.figure_provenance(checked, None, msgs, enforce=False, tool_passages=True)
    out, trail = cp.figure_provenance(checked, None, msgs, enforce=True, tool_passages=True)
    report = {
        "figures_removed": [e["figure"] for e in every if not e.get("source")],
        "passages_read": sum(1 for r in cp.build_evidence(None, msgs, tool_passages=True).records
                             if r.kind == "retrieval"),
    }
    return out, trail, report


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
    evidence: List[Dict[str, Any]] = []  # tool results, whole, for the exit check
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
            full = json.dumps(result, default=str)
            tool_msg = {"role": "tool", "name": name, "tool_call_id": call.get("id") or name}
            # The model gets at most _TOOL_RESULT_CHARS of a result; the exit
            # check reads it whole -- cut, the JSON no longer parses and every
            # figure it backs looks unsourced (live: 23 figures removed).
            msgs.append({**tool_msg, "content": full[:_TOOL_RESULT_CHARS]})
            evidence.append({**tool_msg, "content": full})
    if answer is None:
        answer = _STOP
    whole = iter(evidence)  # same order as the tool messages in msgs
    trail = [next(whole) if m.get("role") == "tool" else m for m in msgs]
    answer, provenance, check = exit_check(answer, trail)
    for word in answer.split(" "):
        yield {"type": "token", "content": word + " "}
    yield {"type": "end", "complete": True, "mode": "driver", "hat": hat, "tools": used, "exports": exports,
           "provenance": provenance, "exit_check": check,
           "steps": step + 1, "elapsed_s": round(time.monotonic() - t0, 2)}
