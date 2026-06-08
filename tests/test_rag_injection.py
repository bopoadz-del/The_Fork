"""Tests for the RAG injection helpers: audit log writer, budget,
noise filter, _rag_inject. The injector + retriever calls are mocked
in this file; we do not hit the live LLM or vector store.
"""
from __future__ import annotations

import json
import os
import pathlib
import tempfile

import pytest


def test_audit_writer_writes_one_jsonl_row_per_call(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.core.rag import audit
    audit.write({
        "timestamp": "2026-06-09T10:00:00Z",
        "project_id": "p1",
        "conversation_id": "ws-p1",
        "user_id": "u1",
        "agent_name": "project-assistant",
        "user_message_preview": "hi",
        "requested_k": 5,
        "injected_k": 5,
        "injected_tokens": 1200,
        "top_score": 0.71,
        "threshold_fired": False,
        "noise_filtered_count": 0,
        "budget_remaining": 498800,
        "budget_degraded": False,
        "chunks": [],
    })
    log_path = tmp_path / "logs" / "rag_audit.jsonl"
    assert log_path.exists()
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["project_id"] == "p1"
    assert rows[0]["injected_tokens"] == 1200


def test_audit_writer_never_raises_on_disk_failure(monkeypatch):
    """Audit writes must never break a real chat turn. If the path is
    unwritable, the writer logs and swallows."""
    from app.core.rag import audit
    # Force the resolved log path to contain a NUL byte. os.makedirs raises
    # ValueError on NUL on every platform, which reliably exercises the
    # writer's failure branch. (A literal unwritable string like
    # "/nonexistent/..." can't be used here: on Windows it resolves under
    # C:\ and the write actually succeeds; and os.environ rejects NUL on
    # the way in, so DATA_DIR can't carry it directly.)
    monkeypatch.setattr(audit, "_log_path", lambda: "\x00invalid/logs/rag_audit.jsonl")
    # Should not raise even though the path is unwritable.
    audit.write({"hello": "world"})


def test_budget_starts_at_zero_consumed_for_new_day(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_DAILY_TOKEN_BUDGET", "500000")
    from app.core.rag import budget
    state = budget.snapshot(day="2026-06-09")
    assert state == {"day": "2026-06-09", "consumed": 0, "budget": 500000, "remaining": 500000, "degraded": False}


def test_budget_consume_accumulates_and_reports_degraded_at_inclusive_boundary(
    monkeypatch, tmp_path,
):
    """The spec says 'consumed >= RAG_DAILY_TOKEN_BUDGET' (inclusive).
    The boundary test: when consumed reaches exactly the budget the
    next snapshot must report degraded=True."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_DAILY_TOKEN_BUDGET", "1000")
    from app.core.rag import budget
    budget.consume(day="2026-06-09", tokens=600)
    st = budget.snapshot(day="2026-06-09")
    assert st["consumed"] == 600 and st["remaining"] == 400 and st["degraded"] is False
    budget.consume(day="2026-06-09", tokens=400)
    # Now consumed == 1000 == budget, EXACTLY at the boundary.
    st = budget.snapshot(day="2026-06-09")
    assert st["consumed"] == 1000
    assert st["remaining"] == 0
    assert st["degraded"] is True, (
        "Boundary semantics: degradation must fire when consumed reaches "
        "the budget exactly. The classic off-by-one would have this still "
        "be False until consumed > budget."
    )


def test_budget_rollover_at_new_day_resets_consumed(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_DAILY_TOKEN_BUDGET", "500000")
    from app.core.rag import budget
    budget.consume(day="2026-06-09", tokens=400000)
    assert budget.snapshot(day="2026-06-09")["consumed"] == 400000
    # New day - implicit rollover by querying a fresh date.
    assert budget.snapshot(day="2026-06-10")["consumed"] == 0
