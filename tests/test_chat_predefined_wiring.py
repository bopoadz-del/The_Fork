"""Phase 1 wiring: /v1/chat/stream routes a known workflow (schedule) to the
orchestrator's predefined reasoning when ORCHESTRATOR_PREDEFINED is on. Intent
decides the form — 'produce' carries a download offer, a question does not."""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
import app.routers.chat as chat_mod

H = {"Authorization": "Bearer cb_dev_key"}


def _events_from_generator(action, message, project_id, monkeypatch, doc_ids=None):
    monkeypatch.setattr("app.core.projects.get_project",
                        lambda pid, user_id=None: {"id": pid, "name": "DC"})

    async def run():
        evs = []
        gen = chat_mod._stream_from_predefined(
            action=action, user_message=message, project_id=project_id,
            user_id="u1", session_id="sess", document_ids=doc_ids or [])
        async for chunk in gen:
            for line in chunk.splitlines():
                if line.startswith("data: "):
                    evs.append(json.loads(line[6:]))
        return evs

    return asyncio.run(run())


def test_predefined_stream_produce_has_offer(monkeypatch):
    evs = _events_from_generator("generate_wbs", "produce the schedule", "proj1", monkeypatch)
    start = next(e for e in evs if e["type"] == "start")
    end = next(e for e in evs if e["type"] == "end")
    assert start["mode"] == "predefined"
    assert end["workflow"] == "generate_wbs"
    assert end["exports"] and end["exports"][0]["endpoint"].endswith("/export/schedule-from-brief")
    text = "".join(e.get("content", "") for e in evs if e["type"] == "token")
    assert "working days" in text and "download" in text.lower()


def test_predefined_stream_question_has_no_offer(monkeypatch):
    evs = _events_from_generator("generate_wbs", "how long is procurement on the schedule?", "proj1", monkeypatch)
    end = next(e for e in evs if e["type"] == "end")
    assert end["exports"] == []
    text = "".join(e.get("content", "") for e in evs if e["type"] == "token")
    assert "download" not in text.lower()


def test_gating_condition(monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_PREDEFINED", raising=False)
    assert not chat_mod._predefined_enabled()
    monkeypatch.setenv("ORCHESTRATOR_PREDEFINED", "1")
    assert chat_mod._predefined_enabled()
    assert "generate_wbs" in chat_mod._predefined_workflows()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_endpoint_routes_to_predefined_when_flagged(client, monkeypatch):
    """With the flag on and a known workflow intent, the endpoint streams the
    predefined path (deterministic, no LLM)."""
    monkeypatch.setenv("ORCHESTRATOR_PREDEFINED", "1")

    async def fake_classify(prompt):
        return ("generate_wbs", 0.9)
    monkeypatch.setattr(chat_mod, "_classify_intent", fake_classify)

    pid = client.post("/v1/projects", json={"name": "PredWire", "client": "A"}, headers=H).json()["id"]
    r = client.post("/v1/chat/stream",
                    json={"prompt": "produce the schedule", "project_id": pid, "target_count": 40},
                    headers=H)
    assert r.status_code == 200
    evs = [json.loads(ln[6:]) for ln in r.text.splitlines() if ln.startswith("data: ")]
    start = next(e for e in evs if e["type"] == "start")
    assert start["mode"] == "predefined"
    end = next(e for e in evs if e["type"] == "end")
    assert end["workflow"] == "generate_wbs"
