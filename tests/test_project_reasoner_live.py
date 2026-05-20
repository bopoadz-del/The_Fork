"""LIVE DeepSeek end-to-end test — Reasoning Engine Plan 5.

Skipped until DEEPSEEK_API_KEY is funded. Acceptance check for the real
reasoning loop. Mock-LLM coverage is in tests/test_project_reasoner.py.
"""

import os

import pytest

from app.blocks.project_reasoner import ProjectReasonerBlock
from app.core.session_store import InMemorySessionStore

pytestmark = pytest.mark.skipif(
    not os.getenv("DEEPSEEK_API_KEY"),
    reason="DEEPSEEK_API_KEY not configured — pending refill",
)


@pytest.mark.asyncio
async def test_live_reasoner_answers_critical_path_question():
    session = InMemorySessionStore().get_or_create("live1")
    session.data["activities"] = [
        {"id": "A", "duration": 3, "predecessors": []},
        {"id": "B", "duration": 5, "predecessors": [{"predecessor_id": "A"}]},
        {"id": "C", "duration": 2, "predecessors": [{"predecessor_id": "B"}]},
    ]
    block = ProjectReasonerBlock()
    out = await block.process({
        "request": "What is the project duration and the critical path?",
        "session": session,
    })
    assert out["status"] == "success"
    assert "10" in out["answer"]


@pytest.mark.asyncio
async def test_live_reasoner_follow_up_uses_prior_state():
    session = InMemorySessionStore().get_or_create("live2")
    session.data["activities"] = [
        {"id": "A", "duration": 3, "predecessors": []},
        {"id": "B", "duration": 5, "predecessors": [{"predecessor_id": "A"}]},
        {"id": "C", "duration": 2, "predecessors": [{"predecessor_id": "B"}]},
    ]
    block = ProjectReasonerBlock()
    await block.process({"request": "Compute the critical path.",
                         "session": session})
    out = await block.process({
        "request": "Now shorten B by 3 days — what is the new duration?",
        "session": session,
    })
    assert out["status"] == "success"
    assert "7" in out["answer"]
