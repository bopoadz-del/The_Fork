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
