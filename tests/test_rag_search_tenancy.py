"""POST /v1/rag/search must not return another tenant's chunks.

Measured on 8535199: a user who is not a member of a project could post
that project's id and receive its chunk text verbatim --

    POST /v1/rag/search {"query": ..., "project_id": <someone else's>}
    -> 200 {"chunks": [{"text": "..."}], "count": 1}

Every other project read surface answers 404 for both "missing" and "not
yours" (no existence leak): /v1/projects/{id}, /documents, /conversations,
/documents/{id}/preview, /export/*, /connectors, and doc_search all route
through projects.get_project_accessible. This route did not, so the gate
existed and one door skipped it.

404 rather than 403 on purpose: a 403 confirms the project exists, which
turns this route into an id-enumeration oracle.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core import jwt_auth
from app.core import users as users_store
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _tenant(tag: str):
    user = users_store.create_user(
        f"rag-{tag}-{uuid.uuid4().hex[:8]}@example.test",
        "Correct-Horse-Battery-9!",
        email_verified=True,
    )
    return user, {"Authorization": f"Bearer {jwt_auth.create_token(user['id'])}"}


@pytest.fixture
def two_tenants(client):
    _owner, h_owner = _tenant("owner")
    _other, h_other = _tenant("other")
    pid = client.post(
        "/v1/projects", headers=h_owner, json={"name": "Tenancy Probe"}
    ).json()["id"]
    r = client.post(
        f"/v1/projects/{pid}/documents",
        headers=h_owner,
        files={
            "file": (
                "notes.txt",
                b"the private rate is 0.1 percent per day " * 40,
                "text/plain",
            )
        },
    )
    assert r.status_code == 201, r.text
    return pid, h_owner, h_other


def test_owner_can_search_own_project(client, two_tenants):
    pid, h_owner, _ = two_tenants
    r = client.post(
        "/v1/rag/search",
        headers=h_owner,
        json={"query": "private rate percent", "project_id": pid, "k": 5},
    )
    assert r.status_code == 200, r.text
    assert r.json()["count"] >= 1


def test_non_member_gets_404_and_no_chunk_text(client, two_tenants):
    pid, _, h_other = two_tenants
    r = client.post(
        "/v1/rag/search",
        headers=h_other,
        json={"query": "private rate percent", "project_id": pid, "k": 5},
    )
    assert r.status_code == 404, r.text
    assert "private rate" not in r.text


def test_unknown_project_is_indistinguishable_from_foreign(client, two_tenants):
    _, h_owner, _ = two_tenants
    r = client.post(
        "/v1/rag/search",
        headers=h_owner,
        json={"query": "x", "project_id": "does-not-exist", "k": 5},
    )
    assert r.status_code == 404


def test_gk_status_uses_the_same_gate(client, two_tenants):
    pid, _, h_other = two_tenants
    r = client.get("/v1/rag/gk-status", headers=h_other, params={"project_id": pid})
    assert r.status_code == 404
