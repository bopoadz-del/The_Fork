"""WAVE 2 B4: compose D599.5 qty + amount when synthesis hangs empty.

Live Master Corpus on 2ceef76 (#545) asked:

    Answer only from the client project documents. What is the quantity
    and amount for breaking out existing carriageway including road
    markings (D599.5)?

Observed FAIL: question echoed, SOURCES CITED empty, raw ~194 chrome,
hung ~212s. No storm-water misroute (#542 already elects the priced
row). B5 PASSed 3,504 m × 80 = 280,320 on the same SHA.

Expected: priced D599.5 340,904 m2 @ 31.00 = 10,568,024.
Must NOT answer Rate Only storm-water / culvert rows.
Kill-switch: COMPOSE_PRICED_BOQ_ROW=0 restores the empty hang.
"""
from __future__ import annotations

import pytest

from app.agents.runtime import (
    Agent,
    _CG_REFUSAL,
    _EMPTY_RESPONSE_FALLBACK,
    _compose_priced_boq_instead_of_retry,
    _final_text_needs_forced_retry,
    _graft_priced_boq_item,
    _graft_rate_only_item,
    _postprocess_answer,
    _should_short_circuit_priced_boq,
)
from app.core.rag.inject import format_chunks_as_system_message
from app.core.rag.retriever import (
    answer_states_priced_boq,
    compose_priced_boq_row,
    format_priced_boq_line,
    priced_boq_compose_enabled,
)
from app.core.rag.vector_store import Chunk


LIVE_PREFIX = "Answer only from the client project documents. "
B4_ASK = (
    "What is the quantity and amount for breaking out existing "
    "carriageway including road markings (D599.5)?"
)
B5_ASK = (
    "What is the amount for removal of existing chain link fence (D549.2)?"
)
G4_ASK = (
    "What is the total amount for removal of storm water culverts (D529.3)?"
)
LIVE_B4 = LIVE_PREFIX + B4_ASK
LIVE_B5 = LIVE_PREFIX + B5_ASK
LIVE_G4 = LIVE_PREFIX + G4_ASK

B4_QTY = "340904"
B4_RATE = "31.00"
B4_AMT = "10568024"
B5_QTY = "3504"
B5_RATE = "80.00"
B5_AMT = "280320"

SOUP = (
    "D529.3 Removal of storm water culverts — m 1,370.00 Rate Only "
    f"D549.2 Removal of existing chain link fence {B5_QTY} m {B5_RATE} "
    f"{B5_AMT}.00 "
    "D599.5 Breaking out existing carriageway including road markings "
    f"{B4_QTY} m2 {B4_RATE} {B4_AMT}"
)
OCR_SPACED = (
    "D 529.3 Removal of storm water culverts — m 1,370.00 Rate Only "
    f"D 549.2 Removal of existing chain link fence 3,504 m {B5_RATE} "
    "280,320.00 "
    "D 599.5 Breaking out existing carriageway including road markings "
    "340,904 m2 31.00 10,568,024"
)
PIPE_SOUP = (
    "| D529.3 | Removal of storm water culverts | — | m | 1,370.00 "
    "| Rate Only | D549.2 | Removal of existing chain link fence | "
    f"{B5_QTY} | m | {B5_RATE} | {B5_AMT} | D599.5 | Breaking out "
    f"existing carriageway including road markings | {B4_QTY} | m2 | "
    f"{B4_RATE} | {B4_AMT} |"
)
EXCLUDED_CULVERT = (
    "J |Breakout and remove existing storm water culverts D599.5 "
    "| sum 1 Excluded"
)
SEARCH_PROMISE = (
    "Let me search the demolition BOQ more specifically for D599.5 "
    "quantity and amount."
)
QUESTION_ECHO = LIVE_B4
CHROME_FLUFF = (
    "Answer only from the client project documents. I will look through "
    "the retrieved excerpts for this item."
)
STORM_WATER = "removal of storm water culverts"


def _sys(*texts: str) -> dict:
    blocks = [
        f"[doc_id=soup chunk={i} score=0.90] {t}"
        for i, t in enumerate(texts)
    ]
    return {"role": "system", "content": "\n\n".join(blocks)}


def _msgs(ask: str) -> list[dict]:
    return [{"role": "user", "content": ask}]


def _has_b4_figures(text: str) -> bool:
    blob = (text or "").replace(",", "")
    return B4_QTY in blob and B4_AMT in blob


def _has_b5_figures(text: str) -> bool:
    blob = (text or "").replace(",", "")
    return B5_AMT in blob


def test_compose_parses_priced_d599_5_from_soup():
    for blob in (SOUP, OCR_SPACED, PIPE_SOUP):
        parsed = compose_priced_boq_row(LIVE_B4, blob)
        assert parsed, blob
        assert _plain(parsed["qty"]) == B4_QTY
        assert _plain(parsed["amount"]) == B4_AMT
        assert "carriageway" in (parsed.get("description") or "").lower()


def test_compose_parses_priced_d549_2_from_soup():
    parsed = compose_priced_boq_row(LIVE_B5, SOUP)
    assert parsed
    assert _plain(parsed["amount"]) == B5_AMT
    assert _plain(parsed["qty"]) == B5_QTY


def test_compose_does_not_invent_from_rate_only_or_excluded():
    assert compose_priced_boq_row(LIVE_G4, SOUP) is None
    assert compose_priced_boq_row(LIVE_B4, EXCLUDED_CULVERT) is None


def test_kill_switch_restores_the_empty_hang(monkeypatch):
    monkeypatch.setenv("COMPOSE_PRICED_BOQ_ROW", "0")
    assert priced_boq_compose_enabled() is False
    assert compose_priced_boq_row(LIVE_B4, SOUP) is None
    rag = _sys(SOUP)
    out = _graft_priced_boq_item("", rag, _msgs(LIVE_B4))
    assert out == ""
    assert not _has_b4_figures(out)


def _plain(value: float) -> str:
    return str(int(round(value)))


def test_graft_replaces_empty_question_echo_and_search_promise():
    rag = _sys(SOUP)
    for hung in ("", QUESTION_ECHO, SEARCH_PROMISE, CHROME_FLUFF,
                 _EMPTY_RESPONSE_FALLBACK, _CG_REFUSAL,
                 "I'm ready to help. Please let me know what you need."):
        out = _graft_priced_boq_item(hung, rag, _msgs(LIVE_B4))
        assert _has_b4_figures(out), hung
        assert STORM_WATER not in out.lower()
        assert "Rate Only" not in out


def test_graft_keeps_an_already_priced_answer():
    rag = _sys(SOUP)
    already = format_priced_boq_line(compose_priced_boq_row(LIVE_B4, SOUP))
    assert _graft_priced_boq_item(already, rag, _msgs(LIVE_B4)) == already


def test_graft_replaces_storm_water_rate_only_misroute():
    rag = _sys(SOUP)
    wrong = "D599.5 (removal of storm water culverts) is Rate Only."
    out = _graft_priced_boq_item(wrong, rag, _msgs(LIVE_B4))
    assert _has_b4_figures(out)
    assert STORM_WATER not in out.lower()


def test_postprocess_empty_hang_states_b4_figures():
    rag = _sys(SOUP)
    out = _postprocess_answer("", rag, _msgs(LIVE_B4))
    assert _has_b4_figures(out)
    assert out != _CG_REFUSAL
    assert STORM_WATER not in out.lower()


def test_postprocess_question_echo_states_b4_figures():
    rag = _sys(SOUP)
    out = _postprocess_answer(QUESTION_ECHO, rag, _msgs(LIVE_B4))
    assert _has_b4_figures(out)
    assert STORM_WATER not in out.lower()


def test_postprocess_b5_empty_still_states_fence_amount():
    rag = _sys(SOUP)
    out = _postprocess_answer("", rag, _msgs(LIVE_B5))
    assert _has_b5_figures(out)
    assert STORM_WATER not in out.lower()


def test_postprocess_g4_still_states_rate_only_not_priced():
    rag = _sys(SOUP)
    out = _postprocess_answer(
        "I'm ready to help. Please let me know what you need.",
        rag, _msgs(LIVE_G4),
    )
    assert "Rate Only" in out
    assert "D529.3" in out
    assert B4_AMT not in out.replace(",", "")


def test_retry_skip_composes_instead_of_another_llm_hop():
    rag = _sys(SOUP)
    assert _final_text_needs_forced_retry(SEARCH_PROMISE, user_message=LIVE_B4)
    filled = _compose_priced_boq_instead_of_retry(
        SEARCH_PROMISE, rag, _msgs(LIVE_B4),
    )
    assert _has_b4_figures(filled)
    parsed = compose_priced_boq_row(LIVE_B4, SOUP)
    assert answer_states_priced_boq(filled, parsed)


def test_retry_skip_is_off_when_kill_switch(monkeypatch):
    monkeypatch.setenv("COMPOSE_PRICED_BOQ_ROW", "0")
    rag = _sys(SOUP)
    assert _compose_priced_boq_instead_of_retry(
        SEARCH_PROMISE, rag, _msgs(LIVE_B4),
    ) == ""


def test_inject_fires_priced_header_on_b4_not_g4():
    chunks = [
        Chunk(
            chunk_id="soup", project_id="p_master", doc_id="soup",
            chunk_index=0, text=SOUP, score=0.9,
        )
    ]
    b4 = format_chunks_as_system_message(chunks, 4, query=LIVE_B4)["content"]
    b5 = format_chunks_as_system_message(chunks, 4, query=LIVE_B5)["content"]
    g4 = format_chunks_as_system_message(chunks, 4, query=LIVE_G4)["content"]
    assert "PRICED BOQ ROW" in b4
    assert "PRICED BOQ ROW" in b5
    assert "RATE ONLY" not in b4
    assert "RATE ONLY" not in b5
    assert "RATE ONLY" in g4
    assert "PRICED BOQ ROW" not in g4
    assert B4_QTY in b4
    assert B5_AMT in b5


def test_rate_only_graft_still_wins_for_g4_on_soup():
    rag = _sys(SOUP)
    out = _graft_rate_only_item(
        "I'm ready to help. Please let me know what you need.",
        rag, _msgs(LIVE_G4),
    )
    assert "Rate Only" in out
    priced = _graft_priced_boq_item(out, rag, _msgs(LIVE_G4))
    assert priced == out


def test_short_circuit_skips_provider_when_priced_row_is_in_rag():
    rag = _sys(SOUP)
    out = _should_short_circuit_priced_boq(
        rag, _msgs(LIVE_B4), has_predispatch=False,
    )
    assert _has_b4_figures(out)
    assert STORM_WATER not in out.lower()
    assert _should_short_circuit_priced_boq(
        rag, _msgs(LIVE_B4), has_predispatch=True,
    ) == ""
    assert _should_short_circuit_priced_boq(
        rag, _msgs(LIVE_G4), has_predispatch=False,
    ) == ""


def test_short_circuit_kill_switch_restores_provider_hop(monkeypatch):
    monkeypatch.setenv("COMPOSE_PRICED_BOQ_ROW", "0")
    rag = _sys(SOUP)
    assert _should_short_circuit_priced_boq(
        rag, _msgs(LIVE_B4), has_predispatch=False,
    ) == ""


@pytest.mark.asyncio
async def test_chat_short_circuits_b4_without_calling_llm(monkeypatch):
    """Live 2ceef76 retest: provider died with the unavailable banner.

    When RAG already has priced D599.5, do not call the LLM. Health can
    stay 49/49 while OpenRouter is owner-gated — compose from excerpts.
    """
    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    monkeypatch.setenv("KIMI_API_KEY", "test-key-not-real")
    monkeypatch.setattr(
        "app.agents.runtime.project_is_rag_ready", lambda _pid: True,
    )
    calls = {"n": 0}

    async def fake_call_llm(*_a, **_k):
        calls["n"] += 1
        return {
            "status": "error",
            "error": "connection refused",
        }

    rag = _sys(SOUP)
    audit = {
        "project_id": "p_master",
        "chunks": [
            {
                "doc_id": "soup",
                "chunk_index": 0,
                "chunk_id": "p_master:soup:0",
                "score": 0.90,
            }
        ],
    }

    def fake_rag_inject(**_k):
        return rag, audit

    agent = Agent(
        name="test-agent",
        description="test",
        system_prompt="You are a test assistant.",
        allowed_blocks=[],
    )
    monkeypatch.setattr(agent, "_call_llm", fake_call_llm)
    monkeypatch.setattr("app.agents.runtime.rag_inject", fake_rag_inject)

    result = await agent.chat(LIVE_B4, project_id="p_master")
    assert calls["n"] == 0
    assert result["status"] == "success"
    assert _has_b4_figures(result["answer"])
    assert STORM_WATER not in result["answer"].lower()
    assert result["iterations"] == 0


@pytest.mark.asyncio
async def test_chat_stream_short_circuits_b4_without_calling_llm(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    monkeypatch.setenv("KIMI_API_KEY", "test-key-not-real")
    monkeypatch.setattr(
        "app.agents.runtime.project_is_rag_ready", lambda _pid: True,
    )
    calls = {"n": 0}

    async def fake_call_llm(*_a, **_k):
        calls["n"] += 1
        raise ConnectionError("connection refused")

    rag = _sys(SOUP)
    audit = {
        "project_id": "p_master",
        "chunks": [
            {
                "doc_id": "soup",
                "chunk_index": 0,
                "chunk_id": "p_master:soup:0",
                "score": 0.90,
            }
        ],
    }
    monkeypatch.setattr(
        "app.agents.runtime.rag_inject",
        lambda **_k: (rag, audit),
    )
    agent = Agent(
        name="test-agent",
        description="test",
        system_prompt="You are a test assistant.",
        allowed_blocks=[],
    )
    monkeypatch.setattr(agent, "_call_llm", fake_call_llm)

    events = []
    async for evt in agent.chat_stream(LIVE_B4, project_id="p_master"):
        events.append(evt)
    assert calls["n"] == 0
    tokens = "".join(
        e.get("content") or "" for e in events if e.get("type") == "token"
    )
    end = next(e for e in events if e.get("type") == "end")
    assert _has_b4_figures(tokens) or _has_b4_figures(end.get("content") or "")
    assert not any(e.get("type") == "error" for e in events)
