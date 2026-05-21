"""Tests for agent-runtime core upgrades — Phase C5 · Stream C.

Covers:
- Backward compatibility (chat unchanged without new args)
- Project-context injection
- Conversation persistence + prior-history loading
- Inter-agent delegation (tool exposure, invocation, loop + depth guards)
- Document-search synthetic tool
- remember_fact synthetic tool

DeepSeek is always MOCKED — Agent._call_llm is monkeypatched to scripted
responses. No network calls.
"""

import importlib

import pytest

from app.agents import runtime as rt
from app.agents.runtime import Agent, MAX_DELEGATION_DEPTH


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_agent(name="test-agent", allowed_blocks=None, can_delegate=False):
    return Agent(
        name=name,
        description="Test agent for unit tests",
        system_prompt="You are a test agent.",
        allowed_blocks=allowed_blocks or [],
        can_delegate=can_delegate,
    )


def _isolate(tmp_path, monkeypatch):
    """Point DATA_DIR at tmp and reload agent_memory + projects so no test
    touches the real DB. Returns the fresh agent_memory module."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app.core.agent_memory as _am
    import app.core.projects as _proj
    import app.core.project_memory as _pm
    importlib.reload(_proj)
    importlib.reload(_pm)
    importlib.reload(_am)
    return _am


def _llm_text(text):
    """A scripted _call_llm coroutine that returns a plain final answer."""
    async def fake(self, messages, api_key, project_id=None):
        return {
            "status": "success",
            "choice": {"message": {"content": text}},
            "raw": {},
        }
    return fake


def _llm_capture(box, text="ok"):
    """A scripted _call_llm that records the messages it received."""
    async def fake(self, messages, api_key, project_id=None):
        box["messages"] = messages
        box["project_id"] = project_id
        return {
            "status": "success",
            "choice": {"message": {"content": text}},
            "raw": {},
        }
    return fake


# ── 1. backward compatibility ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_unchanged_without_new_args(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(Agent, "_call_llm", _llm_text("plain answer"))
    agent = _make_agent()
    out = await agent.chat("hi", api_key="k")
    assert out["status"] == "success"
    assert out["answer"] == "plain answer"
    # No project / conversation → only the system prompt as system message
    sys_msgs = [m for m in out["messages"] if m["role"] == "system"]
    assert len(sys_msgs) == 1
    assert sys_msgs[0]["content"] == agent.system_prompt


# ── 2. project context injection ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_injects_project_context(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    import app.core.projects as store
    proj = store.create_project("C5 Context Project")
    pid = proj["id"]
    store.set_fact(pid, "contract_value", "7777777")

    box = {}
    monkeypatch.setattr(Agent, "_call_llm", _llm_capture(box))
    agent = _make_agent()
    # query mentions "contract value" so build_project_context's fact search matches
    await agent.chat("what is the contract value", api_key="k", project_id=pid)

    sys_text = "\n".join(m["content"] for m in box["messages"] if m["role"] == "system")
    assert "7777777" in sys_text


# ── 3. conversation persistence ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_persists_conversation(tmp_path, monkeypatch):
    am = _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(Agent, "_call_llm", _llm_text("the answer"))
    agent = _make_agent()
    await agent.chat("first", api_key="k", conversation_id="cv1")

    msgs = am.get_messages("cv1")
    contents = [(m["role"], m["content"]) for m in msgs]
    assert ("user", "first") in contents
    assert ("assistant", "the answer") in contents


# ── 4. loads prior history ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_loads_prior_history(tmp_path, monkeypatch):
    am = _isolate(tmp_path, monkeypatch)
    am.get_or_create_conversation("cv-hist", "test-agent")
    am.append_message("cv-hist", "user", "earlier question")
    am.append_message("cv-hist", "assistant", "earlier answer")

    box = {}
    monkeypatch.setattr(Agent, "_call_llm", _llm_capture(box))
    agent = _make_agent()
    await agent.chat("next", api_key="k", conversation_id="cv-hist")

    all_text = "\n".join(str(m.get("content")) for m in box["messages"])
    assert "earlier question" in all_text
    assert "earlier answer" in all_text


# ── 5. delegation tool exposure ──────────────────────────────────────────────

def test_delegating_agent_exposes_delegate_tool():
    delegating = _make_agent(can_delegate=True)
    names = [t["function"]["name"] for t in delegating.tool_definitions()]
    assert "delegate_to_agent" in names

    plain = _make_agent(can_delegate=False)
    plain_names = [t["function"]["name"] for t in plain.tool_definitions()]
    assert "delegate_to_agent" not in plain_names


# ── 6. delegation invokes the target ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_delegation_invokes_target(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    specialist = _make_agent(name="specialist")
    monkeypatch.setitem(rt.AGENT_REGISTRY, "specialist", specialist)
    monkeypatch.setattr(Agent, "_call_llm", _llm_text("specialist says hello"))

    caller = _make_agent(name="caller", can_delegate=True)
    tool_call = {
        "id": "d1",
        "function": {
            "name": "delegate_to_agent",
            "arguments": '{"agent_name": "specialist", "message": "help me"}',
        },
    }
    result = await caller._run_tool_call(tool_call, api_key="k", _call_stack=["caller"])
    assert result["ok"] is True
    assert result["result"]["agent"] == "specialist"
    assert result["result"]["answer"] == "specialist says hello"


# ── 7. delegation loop blocked ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delegation_loop_blocked(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    specialist = _make_agent(name="specialist")
    monkeypatch.setitem(rt.AGENT_REGISTRY, "specialist", specialist)

    caller = _make_agent(name="caller", can_delegate=True)
    tool_call = {
        "id": "d2",
        "function": {
            "name": "delegate_to_agent",
            "arguments": '{"agent_name": "specialist", "message": "loop"}',
        },
    }
    # specialist already in the call stack → loop
    result = await caller._run_tool_call(
        tool_call, api_key="k", _call_stack=["caller", "specialist"]
    )
    assert result["ok"] is False
    assert "loop" in result["result"]["hint"].lower()


# ── 8. delegation depth capped ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delegation_depth_capped(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    specialist = _make_agent(name="specialist")
    monkeypatch.setitem(rt.AGENT_REGISTRY, "specialist", specialist)

    caller = _make_agent(name="caller", can_delegate=True)
    tool_call = {
        "id": "d3",
        "function": {
            "name": "delegate_to_agent",
            "arguments": '{"agent_name": "specialist", "message": "deep"}',
        },
    }
    result = await caller._run_tool_call(
        tool_call, api_key="k", _depth=MAX_DELEGATION_DEPTH, _call_stack=["caller"]
    )
    assert result["ok"] is False
    assert "depth" in result["result"]["hint"].lower()


# ── 9. search tool present only with project_id ──────────────────────────────

def test_search_tool_present_only_with_project_id():
    agent = _make_agent()
    with_pid = [t["function"]["name"] for t in agent.tool_definitions(project_id="p")]
    assert "search_project_documents" in with_pid

    without = [t["function"]["name"] for t in agent.tool_definitions()]
    assert "search_project_documents" not in without


# ── 10. search tool calls doc_index ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_tool_calls_doc_index(monkeypatch):
    sentinel = [{"document_id": "d1", "filename": "spec.pdf",
                 "snippet": "found it", "score": 0.9}]

    async def fake_search(project_id, query, top_k=5):
        return sentinel

    import app.core.doc_index as di
    monkeypatch.setattr(di, "search_project_documents", fake_search)

    agent = _make_agent()
    tool_call = {
        "id": "s1",
        "function": {
            "name": "search_project_documents",
            "arguments": '{"query": "anything"}',
        },
    }
    result = await agent._run_tool_call(tool_call, project_id="proj-x")
    assert result["ok"] is True
    assert result["result"]["results"] == sentinel


@pytest.mark.asyncio
async def test_search_tool_requires_project():
    agent = _make_agent()
    tool_call = {
        "id": "s2",
        "function": {
            "name": "search_project_documents",
            "arguments": '{"query": "anything"}',
        },
    }
    result = await agent._run_tool_call(tool_call)  # no project_id
    assert result["ok"] is False
    assert "project" in result["result"]["hint"].lower()


# ── 11. remember_fact persists ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_remember_fact_persists(tmp_path, monkeypatch):
    am = _isolate(tmp_path, monkeypatch)
    agent = _make_agent(name="rememberer")
    tool_call = {
        "id": "r1",
        "function": {
            "name": "remember_fact",
            "arguments": '{"key": "home_city", "value": "Dubai"}',
        },
    }
    result = await agent._run_tool_call(tool_call)
    assert result["ok"] is True

    facts = am.list_agent_facts("rememberer")
    assert any(f["key"] == "home_city" and f["value"] == "Dubai" for f in facts)


def test_remember_fact_tool_always_present():
    agent = _make_agent()
    names = [t["function"]["name"] for t in agent.tool_definitions()]
    assert "remember_fact" in names


# ── 12. chat_stream basic (unchanged without new args) ───────────────────────

@pytest.mark.asyncio
async def test_chat_stream_unchanged_without_new_args(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(Agent, "_call_llm", _llm_text("streamed answer"))
    agent = _make_agent()

    events = []
    async for ev in agent.chat_stream("hi", api_key="k"):
        events.append(ev)

    types = [ev["type"] for ev in events]
    assert types[0] == "start"
    assert "token" in types
    assert types[-1] == "end"

    token_text = "".join(ev["content"] for ev in events if ev["type"] == "token")
    assert token_text == "streamed answer"


# ── 13. chat_stream persists conversation ────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_stream_persists_conversation(tmp_path, monkeypatch):
    am = _isolate(tmp_path, monkeypatch)
    # Answer is >80 chars so chunking is actually exercised
    long_answer = "This is a long streamed answer that definitely exceeds eighty characters in length, ensuring chunks."
    monkeypatch.setattr(Agent, "_call_llm", _llm_text(long_answer))
    agent = _make_agent()

    async for _ in agent.chat_stream("hello", api_key="k", conversation_id="sv1"):
        pass

    msgs = am.get_messages("sv1")
    roles_contents = [(m["role"], m["content"]) for m in msgs]
    assert ("user", "hello") in roles_contents
    assert ("assistant", long_answer) in roles_contents


# ── 14. chat_stream injects project context ──────────────────────────────────

@pytest.mark.asyncio
async def test_chat_stream_injects_project_context(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    import app.core.projects as store
    proj = store.create_project("Stream Context Project")
    pid = proj["id"]
    store.set_fact(pid, "contract_value", "9999999")

    box = {}
    monkeypatch.setattr(Agent, "_call_llm", _llm_capture(box))
    agent = _make_agent()

    async for _ in agent.chat_stream("what is the contract value", api_key="k", project_id=pid):
        pass

    sys_text = "\n".join(m["content"] for m in box["messages"] if m["role"] == "system")
    assert "9999999" in sys_text


# ── 15. delegation propagates project_id ─────────────────────────────────────

@pytest.mark.asyncio
async def test_delegation_propagates_project_id(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    specialist = _make_agent(name="specialist")
    monkeypatch.setitem(rt.AGENT_REGISTRY, "specialist", specialist)

    box = {}
    monkeypatch.setattr(Agent, "_call_llm", _llm_capture(box, text="specialist answer"))

    caller = _make_agent(name="caller", can_delegate=True)
    tool_call = {
        "id": "d4",
        "function": {
            "name": "delegate_to_agent",
            "arguments": '{"agent_name": "specialist", "message": "sub-task"}',
        },
    }
    result = await caller._run_tool_call(
        tool_call, api_key="k", project_id="proj-xyz", _call_stack=["caller"]
    )
    assert result["ok"] is True
    assert box.get("project_id") == "proj-xyz"
