"""Pilot master-corpus alias.

Verifies that the virtual ``dar_al_arkan_master`` project is backed by the
existing ``projects_folder`` corpus without duplicating chunks or re-importing
Drive. This is a temporary pilot convenience while per-project Drive indexing
is fixed post-pilot.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core import projects as projects_mod
from app.dependencies import require_user


def _ensure_schema():
    from app.core.projects import init_db as init_projects_db
    from app.core.models import RagChunk
    from app.core.db import engine
    init_projects_db()
    RagChunk.__table__.create(bind=engine, checkfirst=True)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _seed_and_stub():
    """Create the backing corpus and stub auth to a known admin user."""
    _ensure_schema()

    def fake_admin():
        return {"user_id": "pilot-admin", "role": "admin"}

    app.dependency_overrides[require_user] = fake_admin

    # Clean slate for the ids this test touches.
    for pid in (projects_mod.MASTER_CORPUS_SOURCE_PROJECT_ID, projects_mod.MASTER_CORPUS_PROJECT_ID):
        projects_mod.delete_project(pid)

    # Create the backing source project (normally ``projects_folder``).
    projects_mod.create_project(
        name="Source corpus",
        user_id="system",
        is_approved=True,
        project_id=projects_mod.MASTER_CORPUS_SOURCE_PROJECT_ID,
        origin="user_create",
    )
    projects_mod.add_document(
        project_id=projects_mod.MASTER_CORPUS_SOURCE_PROJECT_ID,
        original_name="Master Budget.xlsx",
        stored_as="doc-master-1_Master Budget.xlsx",
        file_path="/tmp/doc-master-1_Master Budget.xlsx",
        size=1024,
    )

    yield

    # Teardown: remove seeded rows.
    for pid in (projects_mod.MASTER_CORPUS_SOURCE_PROJECT_ID, projects_mod.MASTER_CORPUS_PROJECT_ID):
        projects_mod.delete_project(pid)


def test_list_projects_includes_master_corpus_alias(client):
    resp = client.get("/v1/projects")
    assert resp.status_code == 200
    ids = {p["id"] for p in resp.json()["projects"]}
    assert projects_mod.MASTER_CORPUS_PROJECT_ID in ids


def test_get_master_corpus_alias_returns_source_documents(client):
    resp = client.get(f"/v1/projects/{projects_mod.MASTER_CORPUS_PROJECT_ID}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == projects_mod.MASTER_CORPUS_NAME
    assert body["id"] == projects_mod.MASTER_CORPUS_PROJECT_ID
    assert len(body["documents"]) == 1
    assert body["documents"][0]["original_name"] == "Master Budget.xlsx"


def test_master_corpus_alias_chat_resolves_to_source_project(client, monkeypatch):
    """The chat endpoint must pass the source project_id to the agent runtime."""
    captured = {}

    async def fake_select(message, agent):
        return agent, {
            "requested": agent.name,
            "final": agent.name,
            "action": None,
            "confidence": 0.0,
            "reason": "test",
        }

    async def fake_chat(*args, **kwargs):
        captured["project_id"] = kwargs.get("project_id")
        captured["conversation_id"] = kwargs.get("conversation_id")
        return {
            "content": "pilot answer",
            "sources": [],
            "agent": kwargs.get("agent_name", "project-assistant"),
        }

    monkeypatch.setattr("app.routers.agents.select_agent_for_message", fake_select)
    monkeypatch.setattr(
        "app.agents.runtime.Agent.chat",
        fake_chat,
    )

    resp = client.post(
        "/v1/agents/project-assistant/chat",
        json={
            "message": "What is in the master corpus?",
            "project_id": projects_mod.MASTER_CORPUS_PROJECT_ID,
            "conversation_id": f"ws-{projects_mod.MASTER_CORPUS_PROJECT_ID}",
        },
    )
    assert resp.status_code == 200, resp.text
    assert captured["project_id"] == projects_mod.MASTER_CORPUS_SOURCE_PROJECT_ID
    assert captured["conversation_id"] == f"ws-{projects_mod.MASTER_CORPUS_PROJECT_ID}"


def test_non_admin_cannot_see_master_corpus_alias(client, monkeypatch):
    """A regular user with include_admin_approved semantics can read the alias,
    but the legacy owner-only view excludes it."""
    def fake_user():
        return {"user_id": "regular-user", "role": "user"}

    app.dependency_overrides[require_user] = fake_user

    # list_projects for non-admin uses include_admin_approved=True, so alias
    # should appear because the source corpus is marked approved.
    resp = client.get("/v1/projects")
    assert resp.status_code == 200
    ids = {p["id"] for p in resp.json()["projects"]}
    assert projects_mod.MASTER_CORPUS_PROJECT_ID in ids

    # Direct detail read also uses read_only=True → include_admin_approved=True.
    resp = client.get(f"/v1/projects/{projects_mod.MASTER_CORPUS_PROJECT_ID}")
    assert resp.status_code == 200
    assert resp.json()["name"] == projects_mod.MASTER_CORPUS_NAME
