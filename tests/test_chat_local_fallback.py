"""Chat block — local-fallback guarantees (no cloud provider names).

Locks in two invariants we never want to regress:

1. The chat block must never go completely dark on the user. When DeepSeek is
   not configured AND no local LLM backend is reachable, ``process()`` must
   still return ``status: success`` with a graceful offline-template message —
   the UI must never see a raw error from the chat block in this scenario.

2. The ChatBlock source must not reference Anthropic / OpenAI / Grok / Claude
   anywhere. Those provider names were deliberately removed from the chat API.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from app.blocks.chat import ChatBlock


@pytest.mark.asyncio
async def test_offline_template_when_no_provider_available(monkeypatch):
    """No DeepSeek key, unreachable local LLM → graceful offline template."""

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("LLAMA_CPP_MODEL_PATH", raising=False)
    # Point Ollama at an unreachable port so the local path fails fast.
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:1")

    block = ChatBlock()
    result = await block.process("Hello, what is 2+2?", {"stream": False})

    assert result["status"] == "success", "chat must not go dark — offline path returns success"
    assert result["provider"] == "offline_template"
    text = result.get("text", "")
    assert "offline mode" in text.lower()
    # The user's message must be echoed back so they know the chat is alive.
    assert "Hello, what is 2+2?" in text
    # The template must surface BOTH error reasons so the operator knows what to fix.
    assert "DEEPSEEK_API_KEY" in text
    assert "ollama" in text.lower() or "llama" in text.lower()


def test_chat_block_source_has_no_forbidden_provider_names():
    """Provider names removed per platform direction — they must not return."""

    src = inspect.getsource(ChatBlock)
    forbidden = ["anthropic", "openai", "grok", "claude"]
    for term in forbidden:
        assert term.lower() not in src.lower(), f"forbidden provider name '{term}' reappeared in ChatBlock"


def test_chat_block_metadata():
    """Surface fields the platform relies on (name/version/tags) — sanity check."""

    assert ChatBlock.name == "chat"
    assert ChatBlock.version.startswith("3.")
    assert "ai" in ChatBlock.tags
    assert "chat" in ChatBlock.tags
