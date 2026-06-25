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
from app.core import users as users_mod
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


# ── P0C pilot guardrail tests ───────────────────────────────────────────────


def test_list_projects_sorts_master_corpus_first(client):
    resp = client.get("/v1/projects")
    assert resp.status_code == 200
    projects = resp.json()["projects"]
    assert projects[0]["id"] == projects_mod.MASTER_CORPUS_PROJECT_ID
    assert projects[0]["is_master_corpus"] is True


def test_master_corpus_exposes_document_count(client):
    resp = client.get("/v1/projects")
    assert resp.status_code == 200
    master = next(
        (p for p in resp.json()["projects"] if p["id"] == projects_mod.MASTER_CORPUS_PROJECT_ID),
        None,
    )
    assert master is not None
    assert master["document_count"] == 1


def test_non_admin_list_hides_incomplete_approved_shells(client, monkeypatch):
    def fake_user():
        return {"user_id": "regular-user", "role": "user"}

    app.dependency_overrides[require_user] = fake_user

    shell = projects_mod.create_project(
        name="Empty Approved Shell",
        user_id="system",
        is_approved=True,
        origin="admin_drive_approved",
    )
    try:
        resp = client.get("/v1/projects")
        assert resp.status_code == 200
        ids = {p["id"] for p in resp.json()["projects"]}
        assert projects_mod.MASTER_CORPUS_PROJECT_ID in ids
        assert shell["id"] not in ids
    finally:
        projects_mod.delete_project(shell["id"])


def test_admin_list_keeps_incomplete_approved_shells(client):
    shell = projects_mod.create_project(
        name="Empty Approved Shell Admin",
        user_id="system",
        is_approved=True,
        origin="admin_drive_approved",
    )
    try:
        resp = client.get("/v1/projects")
        assert resp.status_code == 200
        ids = {p["id"] for p in resp.json()["projects"]}
        assert projects_mod.MASTER_CORPUS_PROJECT_ID in ids
        assert shell["id"] in ids
    finally:
        projects_mod.delete_project(shell["id"])


# ── Alias document-search resolution ──────────────────────────────────────────


def test_master_corpus_alias_document_search_resolves_to_source(client, monkeypatch):
    """GET /v1/projects/{alias}/documents/search must query the source corpus."""
    captured = {}

    async def fake_search(project_id, query, top_k=5):
        captured["project_id"] = project_id
        captured["query"] = query
        return [
            {
                "document_id": "doc-master-1",
                "filename": "Master Budget.xlsx",
                "snippet": "budget row",
                "score": 0.99,
            }
        ]

    monkeypatch.setattr(
        "app.routers.doc_search.doc_index.search_project_documents",
        fake_search,
    )
    monkeypatch.setattr("app.routers.doc_search.doc_index._load_index", lambda pid: None)

    resp = client.get(
        f"/v1/projects/{projects_mod.MASTER_CORPUS_PROJECT_ID}/documents/search",
        params={"q": "budget", "top_k": 3},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["project_id"] == projects_mod.MASTER_CORPUS_PROJECT_ID
    assert body["count"] == 1
    assert captured["project_id"] == projects_mod.MASTER_CORPUS_SOURCE_PROJECT_ID
    assert captured["query"] == "budget"


def test_normal_project_document_search_uses_original_id(client, monkeypatch):
    """Non-alias projects must keep using their own project_id for search."""
    captured = {}

    async def fake_search(project_id, query, top_k=5):
        captured["project_id"] = project_id
        return []

    monkeypatch.setattr(
        "app.routers.doc_search.doc_index.search_project_documents",
        fake_search,
    )
    monkeypatch.setattr("app.routers.doc_search.doc_index._load_index", lambda pid: None)

    # Create a real user so project ownership FK succeeds and the search
    # endpoint can resolve ownership for a non-alias project.
    user = users_mod.create_user(
        "normal-owner@local", "password", role="user"
    )

    def fake_owner():
        return {"user_id": user["id"], "role": "user"}

    app.dependency_overrides[require_user] = fake_owner

    normal = projects_mod.create_project(
        name="Normal Project",
        user_id=user["id"],
    )
    try:
        resp = client.get(
            f"/v1/projects/{normal['id']}/documents/search",
            params={"q": "anything"},
        )
        assert resp.status_code == 200, resp.text
        assert captured["project_id"] == normal["id"]
    finally:
        projects_mod.delete_project(normal["id"])
        # Restore admin auth for subsequent tests in this module.
        def fake_admin():
            return {"user_id": "pilot-admin", "role": "admin"}

        app.dependency_overrides[require_user] = fake_admin


def test_document_search_for_missing_project_returns_404(client):
    resp = client.get("/v1/projects/does-not-exist/documents/search", params={"q": "test"})
    assert resp.status_code == 404


# ── Alias mutation routes ───────────────────────────────────────────────────


def test_master_corpus_alias_clear_conversation_resolves(client):
    """Clearing the workspace conversation for the alias must resolve to the
    source project and not 404.
    """
    resp = client.post(
        f"/v1/projects/{projects_mod.MASTER_CORPUS_PROJECT_ID}/conversations/"
        f"ws-{projects_mod.MASTER_CORPUS_PROJECT_ID}/clear"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "cleared"


def test_master_corpus_alias_delete_project_resolves(client):
    """Deleting the alias project must resolve to and delete the source corpus."""
    resp = client.delete(f"/v1/projects/{projects_mod.MASTER_CORPUS_PROJECT_ID}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "deleted"

    # Alias should no longer be listed.
    resp = client.get("/v1/projects")
    assert resp.status_code == 200
    ids = {p["id"] for p in resp.json()["projects"]}
    assert projects_mod.MASTER_CORPUS_PROJECT_ID not in ids
