"""Tests for the session state store — Reasoning Engine Plan 2."""

import pytest

from app.schemas.project_session import Artifact, Message, ProjectSession


def test_new_session_is_empty():
    s = ProjectSession.new("sess1")
    assert s.id == "sess1"
    assert s.data == {} and s.history == [] and s.artifacts == []
    assert s.created_at and s.updated_at


def test_message_and_artifact_models():
    m = Message(role="user", content="hi", ts="2026-05-20T00:00:00Z")
    assert m.role == "user"
    a = Artifact(name="schedule.xlsx", path="/data/x.xlsx", type="excel")
    assert a.type == "excel"
