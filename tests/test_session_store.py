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


from app.core.session_store import InMemorySessionStore


def test_get_or_create_is_idempotent():
    store = InMemorySessionStore()
    a = store.get_or_create("s1")
    b = store.get_or_create("s1")
    assert a.id == b.id == "s1"
    assert store.get("s1") is not None


def test_get_missing_returns_none():
    assert InMemorySessionStore().get("nope") is None


def test_save_persists_mutations():
    store = InMemorySessionStore()
    s = store.get_or_create("s1")
    s.data["activities"] = [{"id": "A"}]
    s.add_message("user", "create a schedule")
    store.save(s)
    reloaded = store.get("s1")
    assert reloaded.data["activities"] == [{"id": "A"}]
    assert reloaded.history[0].content == "create a schedule"


def test_delete_removes_session():
    store = InMemorySessionStore()
    store.get_or_create("s1")
    assert store.delete("s1") is True
    assert store.get("s1") is None
    assert store.delete("s1") is False


import time as _time


def test_session_expires_after_ttl():
    store = InMemorySessionStore(ttl_seconds=1)
    store.get_or_create("s1")
    assert store.get("s1") is not None
    _time.sleep(1.1)
    assert store.get("s1") is None          # expired and evicted


def test_save_refreshes_ttl():
    store = InMemorySessionStore(ttl_seconds=2)
    s = store.get_or_create("s1")
    _time.sleep(1.2)
    store.save(s)                            # resets the 2s window
    _time.sleep(1.2)
    assert store.get("s1") is not None       # still alive — refreshed
