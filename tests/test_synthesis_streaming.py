"""True token streaming for the forced synthesis call (SYNTHESIS_STREAMING=1).

After a deliverable tool returns, force_synthesis makes the next call tool-free.
When streaming is enabled (DeepSeek/OpenRouter + flag on), that synthesis call streams provider
deltas as token events instead of computing the whole answer then re-chunking it.
These tests cover: the happy path (deltas stream, line-buffered + sanitised),
pre-first-token fallback to the non-streaming path, citation sanitisation on
streamed lines, the empty-final forced retry, the default-off inertness, and the
_stream_synthesis SSE parser + sanitiser chokepoint.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from app.agents.runtime import Agent, _SynthStreamError


# ── DeepSeek config so the streaming gate (provider in the allowlist) opens ──
_DEEPSEEK_CFG = {
    "provider": "deepseek",
    "url": "https://api.deepseek.com/v1/chat/completions",
    "env_key": "DEEPSEEK_API_KEY",
    "default_model": "deepseek-chat",
}


@pytest.fixture(autouse=True)
def _disable_commissioning_remaining(monkeypatch):
    """These tests need the model to emit the commissioning tool_call.

    Remaining predispatch would draft the checklist first and lock
    synthesis before iter-0, so _call_llm would never see a tool hop.
    """
    monkeypatch.setenv("AGENT_COMMISSIONING_PREDISPATCH", "0")


@pytest.fixture
def deepseek_streaming(monkeypatch):
    """Enable the streaming path deterministically: deepseek provider, flag on,
    an api key present."""
    monkeypatch.setattr("app.agents.runtime._llm_config", lambda: dict(_DEEPSEEK_CFG))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SYNTHESIS_STREAMING", "1")


def _pa_agent():
    return Agent(name="project-assistant", description="pa",
                 system_prompt="x", allowed_blocks=["construction"])


def _drain(agen):
    async def go():
        return [ev async for ev in agen]
    return asyncio.run(go())


def _tool_then_final():
    """Fake _call_llm: call 1 -> commissioning tool_call; later calls -> a plain
    final answer. Counts invocations so tests can assert the synthesis call did
    NOT go through _call_llm when streamed."""
    state = {"n": 0}

    async def fake(_self, messages, api_key, **kwargs):
        state["n"] += 1
        if state["n"] == 1:
            return {"status": "success", "choice": {"message": {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "c1", "type": "function", "function": {
                    "name": "commissioning_checklist",
                    "arguments": '{"systems":["electrical"]}'}}]}}}
        return {"status": "success", "choice": {"message": {
            "role": "assistant", "content": "NON-STREAMED fallback answer."}}}

    fake.state = state
    return fake


async def _tool_ok(self, tool_call, **kwargs):
    return {"name": "commissioning_checklist", "ok": True,
            "result": {"status": "success", "checklists_by_system": {"electrical": []}}}


def _mk_stream(deltas):
    async def fake_stream(self, messages, api_key, **kwargs):
        for d in deltas:
            yield d
    return fake_stream


def _mk_stream_raise():
    async def fake_stream(self, messages, api_key, **kwargs):
        raise _SynthStreamError("pre-first-token failure")
        yield ""  # pragma: no cover — marks this as an async generator
    return fake_stream


def _mk_stream_empty():
    async def fake_stream(self, messages, api_key, **kwargs):
        return
        yield ""  # pragma: no cover — async generator that yields nothing
    return fake_stream


def _run_turn(agent):
    return _drain(agent.chat_stream(
        user_message="Generate a commissioning checklist for electrical.",
        history=[], project_id=None, conversation_id=None, user_id=None,
    ))


def _tokens(events):
    return "".join(e.get("content", "") for e in events if e.get("type") == "token")


# ── happy path ────────────────────────────────────────────────────────────────

def test_streamed_synthesis_emits_progressive_tokens(deepseek_streaming):
    call_llm = _tool_then_final()
    deltas = ["- Verify ", "electrical ", "isolation\n", "- Check ", "earthing\n", "Done."]
    with patch.object(Agent, "_call_llm", call_llm), \
         patch.object(Agent, "_run_tool_call", _tool_ok), \
         patch.object(Agent, "_stream_synthesis", _mk_stream(deltas)):
        events = _run_turn(_pa_agent())

    # synthesis was STREAMED, not routed through _call_llm -> only the tool call hit it
    assert call_llm.state["n"] == 1, "synthesis should not call _call_llm when streamed"
    token_events = [e for e in events if e.get("type") == "token"]
    # more than one token event => progressive (line-buffered) streaming, not one burst
    assert len(token_events) >= 2, token_events
    text = _tokens(events)
    assert "Verify electrical isolation" in text
    assert "Check earthing" in text
    assert "Done." in text  # trailing partial line flushed at end
    assert events[-1]["type"] == "end"
    assert "NON-STREAMED" not in text


# ── pre-first-token fallback ──────────────────────────────────────────────────

def test_pretoken_stream_error_falls_back_to_non_streaming(deepseek_streaming):
    call_llm = _tool_then_final()
    with patch.object(Agent, "_call_llm", call_llm), \
         patch.object(Agent, "_run_tool_call", _tool_ok), \
         patch.object(Agent, "_stream_synthesis", _mk_stream_raise()):
        events = _run_turn(_pa_agent())

    # fell back -> synthesis went through _call_llm (2nd invocation)
    assert call_llm.state["n"] == 2, "should fall back to non-streaming _call_llm"
    assert "NON-STREAMED fallback answer." in _tokens(events)
    assert events[-1]["type"] == "end"


# ── citation sanitisation on streamed lines ───────────────────────────────────

def test_streamed_lines_are_citation_sanitised(deepseek_streaming):
    call_llm = _tool_then_final()
    deltas = ["See [source: C:\\\\Users\\\\shimm\\\\data\\\\PRC-406_HSE.pdf, chunk 3]\n"]
    with patch.object(Agent, "_call_llm", call_llm), \
         patch.object(Agent, "_run_tool_call", _tool_ok), \
         patch.object(Agent, "_stream_synthesis", _mk_stream(deltas)):
        events = _run_turn(_pa_agent())
    text = _tokens(events)
    assert "PRC-406_HSE.pdf" in text
    assert "C:\\" not in text and "/Users/" not in text  # raw path stripped


# ── empty-final forced retry preserved ────────────────────────────────────────

def test_empty_stream_triggers_forced_retry(deepseek_streaming):
    call_llm = _tool_then_final()
    with patch.object(Agent, "_call_llm", call_llm), \
         patch.object(Agent, "_run_tool_call", _tool_ok), \
         patch.object(Agent, "_stream_synthesis", _mk_stream_empty()):
        events = _run_turn(_pa_agent())
    # empty stream -> non-streamed forced retry -> _call_llm called a 2nd time
    assert call_llm.state["n"] == 2
    assert "NON-STREAMED fallback answer." in _tokens(events)
    assert events[-1]["type"] == "end"


# ── default off: streaming never engaged ──────────────────────────────────────

def test_flag_off_never_streams(monkeypatch):
    monkeypatch.setattr("app.agents.runtime._llm_config", lambda: dict(_DEEPSEEK_CFG))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("SYNTHESIS_STREAMING", raising=False)  # OFF
    call_llm = _tool_then_final()

    async def _boom_stream(self, *a, **k):
        raise AssertionError("_stream_synthesis must not be called when flag is off")
        yield ""  # pragma: no cover

    with patch.object(Agent, "_call_llm", call_llm), \
         patch.object(Agent, "_run_tool_call", _tool_ok), \
         patch.object(Agent, "_stream_synthesis", _boom_stream):
        events = _run_turn(_pa_agent())
    assert call_llm.state["n"] == 2  # synthesis via non-streaming _call_llm
    assert "NON-STREAMED fallback answer." in _tokens(events)


def test_an_unlisted_provider_never_streams(monkeypatch):
    """The outer gate carries its own provider list. OAI-shaped Ollama is not
    on it, and must not reach _stream_synthesis at all."""
    ollama_cfg = {"provider": "ollama", "url": "http://x/v1/chat/completions",
                  "env_key": "", "default_model": "glm-5.2:cloud"}
    monkeypatch.setattr("app.agents.runtime._llm_config", lambda: dict(ollama_cfg))
    monkeypatch.setenv("SYNTHESIS_STREAMING", "1")
    call_llm = _tool_then_final()

    async def _boom_stream(self, *a, **k):
        raise AssertionError("must not stream on an unlisted provider")
        yield ""  # pragma: no cover

    with patch.object(Agent, "_call_llm", call_llm), \
         patch.object(Agent, "_run_tool_call", _tool_ok), \
         patch.object(Agent, "_stream_synthesis", _boom_stream):
        events = _run_turn(_pa_agent())
    assert call_llm.state["n"] == 2


def test_deepseek_the_production_primary_reaches_the_streaming_path(monkeypatch):
    """The gate that matters in production. SYNTHESIS_STREAMING=1 and
    LLM_PROVIDER=deepseek are both set on the live service; the outer gate's
    provider allowlist must include the primary or the flag is on and does
    nothing — every live turn would silently take the non-streaming fallback.

    Asserted at the OUTER gate specifically: `_stream_synthesis` having a
    DeepSeek allowlist entry is not enough if this gate never calls it.
    """
    monkeypatch.setattr("app.agents.runtime._llm_config", lambda: {
        "provider": "deepseek",
        "url": "https://api.deepseek.com/v1/chat/completions",
        "env_key": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-chat",
    })
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SYNTHESIS_STREAMING", "1")
    call_llm = _tool_then_final()

    with patch.object(Agent, "_call_llm", call_llm), \
         patch.object(Agent, "_run_tool_call", _tool_ok), \
         patch.object(Agent, "_stream_synthesis", _mk_stream(["Isolate ", "the panel."])):
        events = _run_turn(_pa_agent())

    assert call_llm.state["n"] == 1, "synthesis went through _call_llm, not the stream"
    assert "Isolate the panel." in _tokens(events)


def test_streamed_xml_tool_leak_is_not_flushed(deepseek_streaming):
    """Leftover L4 class: SYNTHESIS_STREAMING must not emit XML tool calls."""
    from app.agents.runtime import _TOOL_FORMAT_FALLBACK

    call_llm = _tool_then_final()
    xml = (
        "<function_calls>\n"
        '<invoke name="formula_executor_v2">\n'
        "<parameter name=\"expression\">1+1</parameter>\n"
        "</invoke>\n"
        "</function_calls>\n"
    )
    deltas = [xml[i : i + 18] for i in range(0, len(xml), 18)]
    with patch.object(Agent, "_call_llm", call_llm), \
         patch.object(Agent, "_run_tool_call", _tool_ok), \
         patch.object(Agent, "_stream_synthesis", _mk_stream(deltas)):
        events = _run_turn(_pa_agent())
    text = _tokens(events)
    collapsed = text.lower().replace(" ", "")
    assert "<function_calls" not in collapsed
    assert "<invoke" not in collapsed
    assert _TOOL_FORMAT_FALLBACK[:40] in text
    assert events[-1]["type"] == "end"


@pytest.mark.parametrize("prose_first", [False, True])
def test_streamed_dsml_tool_leak_is_not_flushed(deepseek_streaming, prose_first):
    """Live d8d9573: the guard above knew XML and raw JSON, not DeepSeek's
    DSML, and ``<｜｜DSML｜｜ invoke name="construction_calc">`` reached the
    browser. Both live orders: markup first, and markup after a line of
    prose (which may legitimately have been shown already)."""
    from tests.test_a_failed_tool_is_not_a_deliverable import LIVE_LEAK

    text_in = ("The calculator returned a zeroed result.\n" if prose_first else "") + LIVE_LEAK
    deltas = [text_in[i : i + 11] for i in range(0, len(text_in), 11)]
    with patch.object(Agent, "_call_llm", _tool_then_final()), \
         patch.object(Agent, "_run_tool_call", _tool_ok), \
         patch.object(Agent, "_stream_synthesis", _mk_stream(deltas)):
        events = _run_turn(_pa_agent())
    shown = _tokens(events)
    assert "DSML" not in shown
    assert "invoke" not in shown
    assert "contract_price" not in shown, "tool arguments leaked"
    assert events[-1]["type"] == "end"
    assert "DSML" not in (events[-1].get("content") or "")


def test_a_tool_that_failed_is_offered_again_not_disarmed(deepseek_streaming):
    """The cause behind the leak. Call 1 asks for a calculation that does not
    exist; the tool says so. The next model call must still HAVE tools --
    ``with_tools`` true -- so the model can use the list of valid names it was
    just handed, rather than being forced to answer empty-handed."""
    seen = []

    async def fake_llm(_self, messages, api_key, **kwargs):
        seen.append(kwargs.get("with_tools", True))
        if len(seen) == 1:
            return {"status": "success", "choice": {"message": {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "c1", "type": "function", "function": {
                    "name": "construction_calc",
                    "arguments": '{"calculation":"calculate_delay_damages"}'}}]}}}
        return {"status": "success", "choice": {"message": {
            "role": "assistant", "content": "Recovered: re-ran it as delay_damages_daily."}}}

    async def tool_failed(self, tool_call, **kwargs):
        return {"name": "construction_calc", "ok": True, "result": {
            "status": "error", "error": "Unknown calculation 'calculate_delay_damages'.",
            "available": ["delay_damages_daily"]}}

    streamed = []

    async def spy_stream(self, messages, api_key, **kwargs):
        streamed.append(True)
        yield "should never be reached"

    with patch.object(Agent, "_call_llm", fake_llm), \
         patch.object(Agent, "_run_tool_call", tool_failed), \
         patch.object(Agent, "_stream_synthesis", spy_stream):
        events = _drain(_pa_agent().chat_stream(
            user_message="If Milestone 1 is 30 days late, what are the delay damages?",
            history=[], project_id=None, conversation_id=None, user_id=None,
        ))

    assert not streamed, "a failed tool was treated as the deliverable"
    assert len(seen) >= 2 and seen[1] is not False, (
        f"tools were disarmed after a FAILED call: with_tools={seen}"
    )
    assert "re-ran it as delay_damages_daily" in _tokens(events)


def test_streamed_json_tool_leak_is_not_flushed(deepseek_streaming):
    """UI-PHYS A5: SYNTHESIS_STREAMING must not flush raw tool-call JSON."""
    import json
    from app.agents.runtime import _TOOL_FORMAT_FALLBACK

    call_llm = _tool_then_final()
    leaked = json.dumps({
        "name": "search_project_documents",
        "arguments": {"query": "Delay Damages Contract Data"},
    })
    deltas = [leaked[i : i + 20] for i in range(0, len(leaked), 20)]
    with patch.object(Agent, "_call_llm", call_llm), \
         patch.object(Agent, "_run_tool_call", _tool_ok), \
         patch.object(Agent, "_stream_synthesis", _mk_stream(deltas)):
        events = _run_turn(_pa_agent())
    text = _tokens(events)
    assert leaked not in text
    assert "search_project_documents" not in text
    assert _TOOL_FORMAT_FALLBACK[:40] in text
    assert events[-1]["type"] == "end"
    assert leaked not in (events[-1].get("content") or "")


# ── _stream_synthesis unit: SSE parse + sanitiser chokepoint ──────────────────

class _FakeStreamResponse:
    def __init__(self, lines, status=200):
        self.status_code = status
        self._lines = lines
        self.text = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln

    async def aread(self):
        return b""


class _FakeAsyncClient:
    """Captures the posted payload and returns canned SSE lines."""
    captured = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, json=None, headers=None):
        _FakeAsyncClient.captured["payload"] = json
        _FakeAsyncClient.captured["url"] = url
        lines = [
            'data: {"choices":[{"delta":{"content":"Hello "}}]}',
            'data: {"choices":[{"delta":{"content":"world"}}]}',
            'data: {"choices":[{"delta":{}}],"usage":{"total_tokens":5}}',
            "data: [DONE]",
        ]
        return _FakeStreamResponse(lines)


def test_stream_synthesis_parses_sse_and_sanitises_outbound(monkeypatch):
    monkeypatch.setattr("app.agents.runtime._llm_config", lambda: dict(_DEEPSEEK_CFG))
    monkeypatch.setattr("app.agents.runtime.httpx.AsyncClient", _FakeAsyncClient)
    agent = _pa_agent()
    # A contaminated message with a non-standard `reasoning` field must be
    # stripped by the SAME chokepoint the non-streaming path uses.
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "prior", "reasoning": "SECRET CHAIN OF THOUGHT",
         "tool_calls": [{"id": "x", "type": "function", "extra": "junk",
                         "function": {"name": "f", "arguments": "{}", "bogus": 1}}]},
        {"role": "user", "content": "go"},
    ]

    async def go():
        return [d async for d in agent._stream_synthesis(messages, "k")]

    out = asyncio.run(go())
    assert "".join(out) == "Hello world"
    posted = _FakeAsyncClient.captured["payload"]
    assert posted["stream"] is True
    assert "tools" not in posted  # synthesis offers no tools
    # sanitiser chokepoint applied to the streamed path too
    for m in posted["messages"]:
        assert "reasoning" not in m
        for tc in m.get("tool_calls", []) or []:
            assert set(tc.keys()) <= {"id", "type", "function"}
            assert set(tc.get("function", {}).keys()) <= {"name", "arguments"}
