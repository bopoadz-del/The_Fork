"""Tests for the smart-orchestrator → chat domain-hint integration (Fix #1).

The chat router now consults SmartOrchestratorBlock before invoking the chat
block. When the orchestrator finds a high-confidence intent match, a short
domain-aware sentence is prepended to the user message so the LLM answers
inside the right frame. When no match (or only weak matches), the message
flows through unchanged.
"""

from __future__ import annotations

import pytest

from app.routers.chat import _with_domain_hint


@pytest.mark.asyncio
async def test_hint_prepended_when_message_clearly_about_boq():
    """A message with strong BOQ keywords should get a Bill-of-Quantities hint."""
    msg = "Process this Bill of Quantities and tell me the totals."
    out = await _with_domain_hint(msg)
    assert out != msg
    assert "[Context for your answer:" in out
    assert "Bill of Quantities" in out
    # The original message must still be present.
    assert msg in out


@pytest.mark.asyncio
async def test_no_hint_when_message_is_pure_smalltalk():
    """'Hello there' has no orchestrator match — message must pass through."""
    msg = "Hello, what's up?"
    out = await _with_domain_hint(msg)
    assert out == msg


@pytest.mark.asyncio
async def test_hint_for_specification_keywords():
    msg = "Check this specification against ACI 318 compliance."
    out = await _with_domain_hint(msg)
    assert "[Context for your answer:" in out
    assert "specification" in out.lower()


@pytest.mark.asyncio
async def test_domain_hint_failure_does_not_break_chat(monkeypatch):
    """If the orchestrator block raises, the message must still flow through."""
    from app.dependencies import block_instances

    # Inject a broken orchestrator so _with_domain_hint hits its except branch.
    class BoomOrchestrator:
        async def process(self, *args, **kwargs):
            raise RuntimeError("orchestrator broken")

    block_instances["smart_orchestrator"] = BoomOrchestrator()
    try:
        msg = "Process the Bill of Quantities."
        out = await _with_domain_hint(msg)
        assert out == msg  # unchanged
    finally:
        block_instances.pop("smart_orchestrator", None)
