"""The reasoner picks any of the container's ~55 actions; run_workflow must
dispatch it generically via the container (no per-action wiring) and render a
substantial answer — instead of returning handled=False and falling to a short
chat reply. Regression guard for the 26 features that produced stub answers.
"""
from __future__ import annotations

import asyncio

import app.core.predefined_reasoning as pr
from app.schemas.project_session import ProjectSession


class _FakeContainer:
    def get_actions(self):
        return {"risk_register_auto_populate": object(), "estimate_costs": object()}

    async def execute(self, envelope):
        assert envelope.get("action") == "risk_register_auto_populate"
        return {
            "status": "success",
            "action": "risk_register",
            "total_risks": 3,
            "risks": [
                {"id": "R1", "hazard": "excavation collapse", "severity": "high"},
                {"id": "R2", "hazard": "utility strike", "severity": "medium"},
                {"id": "R3", "hazard": "dewatering", "severity": "low"},
            ],
        }


def _patch_container(monkeypatch):
    monkeypatch.setattr("app.dependencies.get_block_instance",
                        lambda name: _FakeContainer())


def test_generic_dispatch_runs_container_action(monkeypatch):
    _patch_container(monkeypatch)
    ctx = {"message": "populate a risk register", "project_id": "p", "document_ids": [], "params": {}}
    out = asyncio.run(pr.run_workflow("risk_register_auto_populate", ctx,
                                      ProjectSession.new("t", "system")))
    assert out["handled"] is True
    assert out["status"] == "success"
    # The structured payload is rendered into a real answer (risks + hazards),
    # not a stub.
    ans = out["answer"]
    assert "excavation collapse" in ans and "utility strike" in ans
    assert len(ans) > 200


def test_non_container_action_still_falls_through(monkeypatch):
    _patch_container(monkeypatch)
    out = asyncio.run(pr.run_workflow("chit_chat_question", {"message": "hi"},
                                      ProjectSession.new("t", "system")))
    assert out == {"handled": False}


def test_container_action_names_empty_without_kit(monkeypatch):
    # No construction kit / get_block_instance raises -> empty set, never raises.
    def _boom(name):
        raise RuntimeError("construction kit not loaded")
    monkeypatch.setattr("app.dependencies.get_block_instance", _boom)
    assert pr.container_action_names() == set()
