"""A workflow the platform cannot reach must be visible, never silently
missing (ORDERS_2026-10-13T0733Z step 3). Synthetic failure only."""
from __future__ import annotations

import logging

from app.core import predefined_reasoning as pr


def test_a_container_that_does_not_load_is_logged_as_an_error(monkeypatch, caplog):
    import app.dependencies as deps

    def broken(name):
        raise RuntimeError("synthetic container failure")
    monkeypatch.setattr(deps, "get_block_instance", broken)
    with caplog.at_level(logging.ERROR):
        assert pr.container_action_names() == set()
    assert any("workflows are unreachable" in r.getMessage() for r in caplog.records)


def test_health_counts_the_reachable_workflows(monkeypatch):
    from app.routers import health

    monkeypatch.setattr(pr, "container_action_names", lambda: {"syn_action_a", "syn_action_b"})
    out = health._probe_workflows()
    assert out["reachable"] == len(set(pr.WORKFLOW_REGISTRY) | {"syn_action_a", "syn_action_b"})
