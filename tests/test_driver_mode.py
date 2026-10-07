"""Driver mode (F-DRIVER Phase B, slice 1): off by default; the model picks
the hat, tools come from the package registry, tool_choice is auto, nothing
is retrieved automatically, and the step limit holds. Synthetic data and a
scripted model."""
from __future__ import annotations

import asyncio
import json

import pytest

from app.agents import driver
from app.agents.driver import context, llm, loop, tools


@pytest.mark.parametrize("value,expected", [("", False), ("0", False), ("1", True), ("on", True)])
def test_driver_mode_is_off_unless_switched_on(monkeypatch, value, expected):
    monkeypatch.setenv("DRIVER_MODE", value)
    assert driver.enabled() is expected


def _agent():
    from app.agents.runtime import Agent

    return Agent(name="driver-probe", description="probe", system_prompt="", allowed_blocks=[])


def _call(name, args, cid):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _scripted(replies, seen):
    async def fake_call(agent, messages, offered, model="", timeout=120.0):
        seen.append({"messages": [dict(m) for m in messages],
                     "tools": [t["function"]["name"] for t in offered]})
        return {"status": "success", "message": replies.pop(0)}
    return fake_call


def _no_retrieval(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("driver mode must not retrieve automatically")
    import app.agents.runtime as rt
    import app.core.rag.inject as inject

    monkeypatch.setattr(inject, "rag_inject", boom)
    monkeypatch.setattr(rt, "rag_inject", boom)


def _run(agent, **kw):
    async def go():
        return [e async for e in loop.stream(agent, **kw)]
    return asyncio.run(go())


def test_the_model_picks_the_hat_and_gets_that_hats_tools(monkeypatch):
    _no_retrieval(monkeypatch)
    seen = []
    replies = [
        {"content": "", "tool_calls": [_call("select_hat", {"hat": "qaqc"}, "c1")]},
        {"content": "", "tool_calls": [_call("search_project_documents", {"query": "slump limits"}, "c2")]},
        {"content": "The specified slump is 75 mm (spec section 3)."},
    ]
    monkeypatch.setattr(llm, "call", _scripted(replies, seen))
    agent = _agent()
    ran = []

    async def fake_tool(call, api_key=None, project_id=None, conversation_id=None, **kw):
        ran.append(call["function"]["name"])
        return {"ok": True, "result": {"chunks": ["slump 75 mm"]}}

    monkeypatch.setattr(agent, "_run_tool_call", fake_tool)
    events = _run(agent, user_message="What slump does the spec allow?", project_id="synthetic-proj")

    assert events[0] == {"type": "start", "mode": "driver", "agent": "driver-probe"}
    end = events[-1]
    assert end["type"] == "end" and end["mode"] == "driver"
    assert end["hat"] == "qaqc" and end["tools"] == ["search_project_documents"]
    assert ran == ["search_project_documents"]  # select_hat is the driver's own
    text = "".join(e["content"] for e in events if e["type"] == "token")
    assert "75 mm" in text
    # Before the hat: base tools only. After: the qaqc package's tools too, no other hat's.
    assert "commissioning_checklist" not in seen[0]["tools"]
    assert "commissioning_checklist" in seen[1]["tools"]
    assert "cash_flow_forecast" not in seen[1]["tools"]
    assert "select_hat" in seen[0]["tools"] and "search_project_documents" in seen[0]["tools"]
    assert "qaqc hat" in seen[1]["messages"][0]["content"]


def test_an_unknown_hat_is_refused_and_the_turn_continues(monkeypatch):
    _no_retrieval(monkeypatch)
    seen = []
    replies = [
        {"content": "", "tool_calls": [_call("select_hat", {"hat": "astrology"}, "c1")]},
        {"content": "A general answer."},
    ]
    monkeypatch.setattr(llm, "call", _scripted(replies, seen))
    events = _run(_agent(), user_message="hello")
    assert events[-1]["hat"] is None
    refused = json.loads(seen[1]["messages"][-1]["content"])
    assert refused["ok"] is False and "astrology" in refused["error"]


def test_the_step_limit_holds(monkeypatch):
    _no_retrieval(monkeypatch)
    monkeypatch.setenv("DRIVER_MAX_STEPS", "2")
    seen = []
    replies = [{"content": "", "tool_calls": [_call("select_hat", {"hat": "planning"}, f"c{i}")]}
               for i in range(5)]
    monkeypatch.setattr(llm, "call", _scripted(replies, seen))
    events = _run(_agent(), user_message="keep going")
    assert len(seen) == 2
    assert events[-1]["steps"] == 2
    assert "within the steps" in "".join(e["content"] for e in events if e["type"] == "token")


def test_the_system_message_carries_rules_hats_and_profile():
    msg = context.system_message(None, None, None)["content"]
    assert context.standing_rules() in msg
    for h in context.hats():
        assert f"- {h['discipline']}: " in msg
    assert "No project is open" in msg


def test_the_model_call_lets_the_model_choose(monkeypatch):
    sent = {}

    class FakeResp:
        status_code = 200

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "ok"}}]}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            sent.update(json)
            return FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-test-key")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    offered = [tools.select_hat_schema(["qaqc", "planning"])]
    out = asyncio.run(llm.call(_agent(), [{"role": "user", "content": "hi"}], offered))
    assert out == {"status": "success", "message": {"content": "ok"}}
    assert sent["tool_choice"] == "auto"
    assert [t["function"]["name"] for t in sent["tools"]] == ["select_hat"]


def _stream(agent, **kw):
    async def go():
        return [e async for e in agent.chat_stream(**kw)]
    return asyncio.run(go())


def test_chat_stream_runs_the_driver_when_driver_mode_is_on(monkeypatch):
    _no_retrieval(monkeypatch)
    monkeypatch.setenv("DRIVER_MODE", "1")
    seen = []
    monkeypatch.setattr(llm, "call", _scripted([{"content": "Driver answer."}], seen))
    agent = _agent()

    async def old_path(*a, **k):
        raise AssertionError("the old path ran in driver mode")
        yield  # pragma: no cover

    monkeypatch.setattr(type(agent), "_chat_stream_impl", old_path)
    events = _stream(agent, user_message="hello")
    assert any(e.get("type") == "start" and e.get("mode") == "driver" for e in events)
    assert "Driver answer." in "".join(e.get("content", "") for e in events if e.get("type") == "token")


def test_chat_stream_keeps_the_old_path_when_driver_mode_is_off(monkeypatch):
    monkeypatch.delenv("DRIVER_MODE", raising=False)
    agent = _agent()

    async def old_path(self, **k):
        yield {"type": "token", "content": "old path"}
        yield {"type": "end", "complete": True}

    async def no_driver(*a, **k):
        raise AssertionError("the driver ran with DRIVER_MODE off")
        yield  # pragma: no cover

    monkeypatch.setattr(type(agent), "_chat_stream_impl", old_path)
    monkeypatch.setattr(loop, "stream", no_driver)
    events = _stream(agent, user_message="hello")
    assert "old path" in "".join(e.get("content", "") for e in events if e.get("type") == "token")


def test_general_knowledge_is_a_tool_the_driver_offers(monkeypatch):
    agent = _agent()
    names = [t["function"]["name"] for t in tools.offered(agent, "synthetic-proj", None, ["qaqc"])]
    assert "search_general_knowledge" in names


def test_search_general_knowledge_searches_the_library_only(monkeypatch):
    from app.agents.core import tool_registry
    from app.core import doc_index
    from app.core.rag import retriever

    asked = []

    async def fake_search(project_id, query, top_k):
        asked.append(project_id)
        return [{"text": f"{project_id} passage", "score": 0.9 if project_id == "gk-b" else 0.4}]

    monkeypatch.setattr(retriever, "_general_knowledge_project_ids", lambda: ["gk-a", "gk-b"])
    monkeypatch.setattr(doc_index, "search_project_documents", fake_search)
    spec = tool_registry.get("search_general_knowledge")
    call = tool_registry.ToolCall(agent=None, name="search_general_knowledge",
                                  args={"query": "retention release", "top_k": 1}, project_id="the-users-project")
    out = asyncio.run(spec.handler(call))
    assert asked == ["gk-a", "gk-b"]  # the library, never the user's project
    assert out["ok"] and [h["project_id"] for h in out["result"]["results"]] == ["gk-b"]
    assert out["result"]["results"][0]["layer"] == "general_knowledge"


def test_the_route_catalogue_is_a_tool_the_model_calls(monkeypatch):
    _no_retrieval(monkeypatch)
    from app.agents.driver import routes
    from app.core import predefined_reasoning

    monkeypatch.setattr(routes, "catalogue", lambda: ["generate_wbs", "resource_histogram"])
    ran = {}

    async def fake_run_workflow(action, ctx, session):
        ran.update(action=action, message=ctx["message"], deliverable=ctx.get("deliverable"))
        return {"handled": True, "answer": "WBS: 3 levels, 12 activities.",
                "exports": [{"label": "WBS (xlsx)", "format": "xlsx"}]}

    monkeypatch.setattr(predefined_reasoning, "run_workflow", fake_run_workflow)
    seen = []
    replies = [
        {"content": "", "tool_calls": [_call("run_workflow", {"workflow": "generate_wbs", "deliverable": True}, "w1")]},
        {"content": "Here is the WBS: 3 levels, 12 activities."},
    ]
    monkeypatch.setattr(llm, "call", _scripted(replies, seen))
    events = _run(_agent(), user_message="build the WBS for a two-storey clinic")
    assert "run_workflow" in seen[0]["tools"]
    assert ran == {"action": "generate_wbs", "message": "build the WBS for a two-storey clinic",
                   "deliverable": True}
    end = events[-1]
    assert end["tools"] == ["run_workflow:generate_wbs"]
    assert end["exports"] == [{"label": "WBS (xlsx)", "format": "xlsx"}]


def test_an_unknown_workflow_is_refused(monkeypatch):
    from app.agents.driver import routes

    monkeypatch.setattr(routes, "catalogue", lambda: ["generate_wbs"])
    out = asyncio.run(routes.run({"workflow": "invent_a_bridge"}, "x", None, None, None))
    assert out["ok"] is False and "invent_a_bridge" in out["error"]
