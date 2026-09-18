"""A lookup question must not be swapped onto heavy-reasoning.

This is about the SHAPE OF THE MESSAGE, never about which project it was asked
in. The corpus is one-to-all at this stage of the platform: there is no
"general knowledge" tier and no per-project tier, and nothing here may
introduce one. The guard reads the message and the classifier confidence and
nothing else -- a test below pins that it never looks at a project id.

Live on 467d7a7, ``POST /v1/chat/stream``:

    "What is the difference between EOT and prolongation cost?"

classified ``forensic_delay_analysis`` at confidence 0.2 and the turn was
handed from project-assistant to heavy-reasoning with
``reason: needs_planning``. It is a definition question. There is no schedule,
no baseline and nothing to plan.

The guard for exactly this class already existed.
``lookup_question_hijack`` was written after the 2026-07-24 repro, where "eot"
sent a notice-period question to the same action -- but it was only wired
into the predefined-dispatch interception in ``app/routers/agents.py``. The
decision that actually *changes agents* lives in
``select_agent_for_message``, one layer down, and was never guarded. So the
fix held on one path and the class kept happening on the other.

The second half of this file is the control: a deliverable-verbed ask, and a
confident route, must still reach heavy-reasoning. A guard that stopped those
would be a quieter and worse bug than the one it fixes.
"""
from __future__ import annotations

import asyncio

import pytest

from app.agents import runtime as runtime_module
from app.agents.runtime import Agent, select_agent_for_message
from tests.conftest import requires_construction_kit

# Exact live wording. Do not paraphrase.
LIVE_EOT_VS_PROLONGATION = "What is the difference between EOT and prolongation cost?"


def _make_agent(name: str) -> Agent:
    return Agent(
        name=name,
        description=name,
        system_prompt=f"you are {name}",
        allowed_blocks=[],
    )


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def registry(monkeypatch):
    pa = _make_agent("project-assistant")
    heavy = _make_agent("heavy-reasoning")
    monkeypatch.setattr(runtime_module, "_SMART_ORCH_BLOCK_CACHE", None)
    runtime_module.AGENT_REGISTRY.clear()
    runtime_module.AGENT_REGISTRY["project-assistant"] = pa
    runtime_module.AGENT_REGISTRY["heavy-reasoning"] = heavy
    try:
        yield pa, heavy
    finally:
        runtime_module.AGENT_REGISTRY.clear()


@requires_construction_kit
def test_the_live_question_stays_on_project_assistant(registry):
    pa, _heavy = registry
    final, routing = _run(select_agent_for_message(LIVE_EOT_VS_PROLONGATION, pa))

    assert final is pa, routing
    assert routing["final"] == "project-assistant", routing
    assert routing["reason"] != "needs_planning", routing


@requires_construction_kit
@pytest.mark.parametrize(
    "message",
    [
        "What is a forensic delay analysis?",
        "How does earned value management work?",
        "Is a resource histogram the same as a manpower curve?",
    ],
)
def test_definition_questions_are_not_planning_tasks(registry, message):
    """Shape-invariance: the class, not just the one sentence that was caught."""
    pa, _heavy = registry
    final, routing = _run(select_agent_for_message(message, pa))

    assert final is pa, routing
    assert routing["reason"] != "needs_planning", routing


@requires_construction_kit
@pytest.mark.parametrize(
    "message",
    [
        # Deliverable verbs mean "make me the artifact", question mark or not.
        "Generate a WBS for a 10-floor residential tower",
        "Prepare an EOT claim for the 30-day delay to Milestone 1",
    ],
)
def test_deliverable_asks_still_reach_heavy_reasoning(registry, message):
    """The control. The guard must not stop the routes it was never about."""
    _pa, _heavy = registry
    pa = runtime_module.AGENT_REGISTRY["project-assistant"]
    final, routing = _run(select_agent_for_message(message, pa))

    assert final.name == "heavy-reasoning", routing
    assert routing["reason"] == "needs_planning", routing


def test_a_guard_failure_cannot_break_routing(registry, monkeypatch):
    """Routing is best-effort. If the guard itself raises, the message must
    still be routed by the rules that were there before it."""
    import app.core.predefined_reasoning as predefined

    def boom(*_args, **_kwargs):
        raise RuntimeError("planted")

    monkeypatch.setattr(predefined, "lookup_question_hijack", boom)
    pa, _heavy = registry
    final, routing = _run(select_agent_for_message(LIVE_EOT_VS_PROLONGATION, pa))

    assert final is not None
    assert "reason" in routing


def test_the_guard_never_looks_at_which_project_was_asked():
    """One-to-all corpus: routing a lookup question must not depend on the
    project. Pinned on the signature, so nobody can add a project-aware branch
    here without this test naming what they just introduced."""
    import inspect

    from app.core.predefined_reasoning import lookup_question_hijack

    params = set(inspect.signature(lookup_question_hijack).parameters)
    assert params == {"message", "confidence"}, params
    assert not any("project" in name for name in params)
