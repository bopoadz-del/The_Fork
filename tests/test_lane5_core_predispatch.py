"""CORE predispatch and mode. A driver-header turn stays on the driver,
an ask runs the workflow it names, a missing model key still runs a
finished calculator, and empty-model recovery is prose."""
from __future__ import annotations

import json

import pytest

from app.core.predefined_reasoning import DELIVER_REGISTRY, WORKFLOW_REGISTRY
from app.schemas.execution_plan import ExecutionPlan
from tests.lane5_core_support import (
    Param, answer_text, call, construction_agent, drive, registered,
)


@pytest.fixture(autouse=True)
def _driver_stream(monkeypatch):
    from tests.lane5_core_support import install_driver_stream

    install_driver_stream(monkeypatch)


def _plan(_context):
    return ExecutionPlan(steps=[])


def _install_workflows():
    def deliver_panel(_session, _deliverable, _message):
        return "The panel ledger total is 17."

    def deliver_north(_session, _deliverable, _message):
        return "The north ledger total is 19."

    deliver_panel.display_name = "Panel ledger"
    deliver_north.display_name = "North ledger"
    saved = {key: WORKFLOW_REGISTRY.get(key) for key in ("lane5_panel_ledger", "lane5_north_ledger")}
    saved_d = {key: DELIVER_REGISTRY.get(key) for key in saved}
    WORKFLOW_REGISTRY["lane5_panel_ledger"] = _plan
    WORKFLOW_REGISTRY["lane5_north_ledger"] = _plan
    DELIVER_REGISTRY["lane5_panel_ledger"] = deliver_panel
    DELIVER_REGISTRY["lane5_north_ledger"] = deliver_north
    return saved, saved_d


def _restore_workflows(saved, saved_d):
    for key, value in saved.items():
        if value is None:
            WORKFLOW_REGISTRY.pop(key, None)
        else:
            WORKFLOW_REGISTRY[key] = value
    for key, value in saved_d.items():
        if value is None:
            DELIVER_REGISTRY.pop(key, None)
        else:
            DELIVER_REGISTRY[key] = value


def _intent(action):
    async def fake(_prompt, has_documents=False):
        return {"action": action, "workflow": action, "mode": "deliverable", "params": {}}

    return fake


def _post(monkeypatch, message, action):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("DRIVER_MODE", "request")
    monkeypatch.setenv("ORCHESTRATOR_PREDEFINED", "1")
    monkeypatch.setattr("app.routers.chat.understand_intent", _intent(action))
    from app.agents.driver import llm
    from tests.lane5_core_support import scripted

    seen: list = []
    monkeypatch.setattr(llm, "call", scripted([
        {"content": "", "tool_calls": [call("run_workflow", {
            "workflow": "lane5_panel_ledger", "deliverable": False,
        }, "c1")]},
        {"content": "From the driver. The panel ledger total is 17."},
    ], seen))
    body = {"message": message, "project_id": None, "history": []}
    headers = {"Authorization": "Bearer cb_dev_key", "X-Fork-Driver": "on"}
    with TestClient(app) as client:
        raw = client.post("/v1/chat/stream", json=body, headers=headers).text
    events = [
        json.loads(line[len("data: "):])
        for line in raw.split("\n") if line.startswith("data: ")
    ]
    return events


def test_the_named_workflow_is_the_one_that_runs(monkeypatch):
    saved = _install_workflows()
    try:
        events = _post(monkeypatch, "Prepare the panel ledger.", "lane5_north_ledger")
    finally:
        _restore_workflows(*saved)
    text = "".join(event.get("content", "") for event in events if event.get("type") == "token")
    assert "panel ledger total is 17" in text
    assert "north ledger total is 19" not in text


@pytest.mark.xfail(strict=True, reason="CORE: 10 a percent outside 0-100 is rejected and not computed")
def test_a_percent_outside_0_to_100_is_rejected(monkeypatch):
    name = "lane5_hold_gate"

    def fn(holdback_percent: float) -> dict:
        return {"value": holdback_percent}

    user = "calculate the qxnet hold gate holdback 150%"
    with registered(name, fn, "Qxnet hold gate", {
        "holdback_percent": Param("fraction", lo=0, hi=1, label="holdback"),
    }, {"value": "fraction"}):
        _events, seen = drive(monkeypatch, user, [
            {"content": "", "tool_calls": [call("construction_calc", {
                "calculation": name, "text": user,
            }, "c1")]},
            {"content": "noted"},
        ])
    from tests.lane5_core_support import tool_envelope

    env = tool_envelope(seen)
    assert env.get("status") != "success", env
    assert env.get("needs_input") is True


@pytest.mark.xfail(strict=True, reason="CORE: 12 predispatch answers do not credit the tool display name, and a draft repeats the ask")
def test_a_workflow_answer_credits_its_display_name_and_is_not_the_ask(monkeypatch):
    saved = _install_workflows()
    user = "Prepare the panel ledger."

    def deliver(_session, _deliverable, _message):
        return "The stored total is 17."

    deliver.display_name = "Panel ledger"
    DELIVER_REGISTRY["lane5_panel_ledger"] = deliver
    try:
        events, _seen = drive(monkeypatch, user, [
            {"content": "", "tool_calls": [call("run_workflow", {
                "workflow": "lane5_panel_ledger", "deliverable": False,
            }, "c1")]},
            {"content": "The stored total is 17."},
        ])
    finally:
        _restore_workflows(*saved)
    text = answer_text(events)
    assert text.strip() != user
    assert "Panel ledger" in text
    assert "lane5_panel_ledger" not in text


def test_a_driver_header_is_not_answered_by_a_predefined_workflow(monkeypatch):
    saved = _install_workflows()
    try:
        events = _post(monkeypatch, "Prepare the panel ledger.", "lane5_panel_ledger")
    finally:
        _restore_workflows(*saved)
    assert any(event.get("type") == "start" and event.get("mode") == "driver" for event in events)
    text = "".join(event.get("content", "") for event in events if event.get("type") == "token")
    assert "From the driver." in text


@pytest.mark.xfail(strict=True, reason="CORE: 23 a tool error shows raw parameter names")
def test_a_tool_error_uses_the_display_label(monkeypatch):
    name = "lane5_mode_label"

    def fn(alpha_m: float) -> dict:
        return {"value": alpha_m}

    user = "calculate the qxnet label gate"
    with registered(name, fn, "Qxnet label gate", {
        "alpha_m": Param("m", lo=0, hi=1e6, label="clear span"),
    }, {"value": "m"}):
        events, seen = drive(monkeypatch, user, [
            {"content": "", "tool_calls": [call("construction_calc", {
                "calculation": name,
            }, "c1")]},
            {"content": "noted"},
        ])
    from tests.lane5_core_support import tool_envelope

    env = tool_envelope(seen)
    blob = str(env.get("error") or env.get("question") or "") + answer_text(events)
    assert "clear span" in blob
    assert "alpha_m" not in blob


@pytest.mark.xfail(strict=True, reason="CORE: 24 empty-model recovery on chat stream prints raw calculator JSON")
def test_empty_model_recovery_is_not_raw_calculator_json(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("DRIVER_MODE", "")
    name = "lane5_mode_recover"

    def fn(span_m: float, breadth_m: float) -> dict:
        return {"value": span_m * breadth_m}

    user = "calculate the qxnet kestrel total span 4 m breadth 3 m"
    probe = construction_agent()

    async def fake(_self, _messages, api_key=None, **_kwargs):
        return {"status": "success", "choice": {"message": {"role": "assistant", "content": ""}}}

    probe._call_llm = fake.__get__(probe, type(probe))
    with registered(name, fn, "Qxnet kestrel total", {
        "span_m": Param("m", lo=0, hi=1e6, label="span"),
        "breadth_m": Param("m", lo=0, hi=1e6, label="breadth"),
    }, {"value": "m2"}):
        import asyncio

        async def go():
            return [event async for event in probe.chat_stream(
                user_message=user, api_key="synthetic-test-key",
            )]

        events = asyncio.run(go())
    text = answer_text(events)
    assert '"status"' not in text
    assert not text.lstrip().startswith("{")
    assert "12" in text


@pytest.mark.xfail(strict=True, reason="CORE: 25 chat stream returns before any hat runs when the model key is missing")
def test_a_missing_model_key_still_runs_a_finished_calculator(monkeypatch):
    monkeypatch.setenv("DRIVER_MODE", "")
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    name = "lane5_mode_nokey"

    def fn(span_m: float, breadth_m: float) -> dict:
        return {"value": span_m * breadth_m}

    user = "calculate the qxnet kestrel total span 4 m breadth 3 m"
    probe = construction_agent()
    with registered(name, fn, "Qxnet kestrel total", {
        "span_m": Param("m", lo=0, hi=1e6, label="span"),
        "breadth_m": Param("m", lo=0, hi=1e6, label="breadth"),
    }, {"value": "m2"}):
        import asyncio

        async def go():
            return [event async for event in probe.chat_stream(
                user_message=user, api_key=None,
            )]

        events = asyncio.run(go())
    text = answer_text(events)
    errors = [event for event in events if event.get("type") == "error"]
    assert not errors
    assert "12" in text
