"""A reasoning model's max_tokens must cover its reasoning, or content is
structurally impossible.

Live-fire phase 3, agent defects D1 + D2: the validation agent and the
smart-orchestrator -- exactly the two agents configured with max_tokens: 1024
-- both returned the canned "I was unable to generate a response" after long
waits. Proven directly against the Moonshot API with the validation agent's
own prompt:

    max_tokens=1024 -> finish_reason=length, content=0 chars, reasoning=3,940
    max_tokens=4096 -> finish_reason=stop,   content=3,977 chars (full report)

K2 reasoning models spend the shared budget on chain-of-thought FIRST; any cap
below the burn guarantees empty content, the empty-final forced retry runs
with the same cap and fails identically, and the user gets the canned message.

The floor is declared per-provider (like fixed_temperature) and applied at the
same outbound chokepoint, so every attempt -- primary, fallback, forced retry
-- is shaped consistently, and non-reasoning providers keep the agent's own
budget untouched.
"""
from __future__ import annotations

import app.agents.runtime as rt


def test_kimi_config_declares_a_reasoning_floor(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    cfg = rt._llm_config()
    assert cfg["provider"] == "kimi"
    assert cfg.get("reasoning_min_tokens", 0) >= 4096, cfg


def test_floor_lifts_a_starving_budget():
    cfg = {"provider": "kimi", "reasoning_min_tokens": 4096}
    assert rt._provider_max_tokens(cfg, 1024) == 4096


def test_floor_does_not_shrink_a_generous_budget():
    cfg = {"provider": "kimi", "reasoning_min_tokens": 4096}
    assert rt._provider_max_tokens(cfg, 8192) == 8192


def test_non_reasoning_provider_keeps_the_agents_budget():
    assert rt._provider_max_tokens({"provider": "groq"}, 1024) == 1024


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    monkeypatch.setenv("KIMI_REASONING_MIN_TOKENS", "2048")
    cfg = rt._llm_config()
    assert cfg.get("reasoning_min_tokens") == 2048
