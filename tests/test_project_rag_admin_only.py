"""Who may add to a project's knowledge base.

1. A project upload (the Documents panel, and ``/upload`` with a project)
   is stored and indexed for the caller who was allowed to attach it.
2. The admin path still adds, the same way.
3. Layer classification is unchanged: an interactive upload carries
   ``provenance=user_upload``, which becomes ``user_session`` only when
   ``RAG_LAYERED`` is on.

``privileges.caller_may_add_to_project_rag`` still gates the routes whose
only job is pulling a corpus in (Drive folder index, Drive import, Aconex
sync / event ingest): those answer 403 to a non-admin. A priced-BOQ export
still downloads for everyone and adds the file to the RAG only for an admin.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from app.core import privileges
from app.core import projects as store
from app.dependencies import require_api_key, require_user
from app.main import app

USER = {"user_id": "rag-rule-user", "role": "user"}
ADMIN = {"user_id": "rag-rule-admin", "role": "admin"}
NO_KEY = {"Authorization": "Bearer unused-dependency-is-overridden"}

SAMPLE_CATEGORIES = [
    {"name": "Site Works", "items": [
        {"item_no": "A.1", "description": "Site clearing", "unit": "Lot", "qty": 1, "rate": 120000},
    ]},
]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def as_caller():
    def _set(principal: Dict[str, Any]) -> None:
        app.dependency_overrides[require_user] = lambda: dict(principal)
        app.dependency_overrides[require_api_key] = lambda: dict(principal)

    yield _set
    app.dependency_overrides.pop(require_user, None)
    app.dependency_overrides.pop(require_api_key, None)


@pytest.fixture
def indexed(monkeypatch) -> List[tuple]:
    """Every route schedules indexing through doc_index.maybe_eager_index."""
    from app.core import doc_index

    calls: List[tuple] = []
    monkeypatch.setattr(doc_index, "maybe_eager_index", lambda pid, did: calls.append((pid, did)))
    return calls


def _project_owned_by(principal: Dict[str, Any]) -> str:
    from app.core import users

    users.ensure_user_exists(principal["user_id"])
    return store.create_project(name=f"RAG rule {principal['role']}", user_id=principal["user_id"])["id"]


def _post_document(client, pid: str):
    files = {"file": ("site-note.txt", b"pour sequence for L3 slab", "text/plain")}
    return client.post(f"/v1/projects/{pid}/documents", files=files, headers=NO_KEY)


# ── the rule itself ────────────────────────────────────────────────────────


def test_only_an_admin_may_add_to_project_rag():
    assert privileges.caller_may_add_to_project_rag("admin") is True
    for role in ("user", "", None, "Admin", "viewer"):
        privileges.set_caller_role(None)
        assert privileges.caller_may_add_to_project_rag(role) is False, role
    privileges.set_caller_role("admin")
    try:
        assert privileges.caller_may_add_to_project_rag() is True
    finally:
        privileges.set_caller_role(None)
    assert privileges.caller_may_add_to_project_rag() is False


# ── rule 1: a user's upload is stored and indexed ─────────────────────────


def test_user_project_upload_is_stored_and_indexed(client, as_caller, indexed):
    as_caller(USER)
    pid = _project_owned_by(USER)

    r = _post_document(client, pid)

    assert r.status_code == 201, r.text
    doc = r.json()["document"]
    assert indexed == [(store.storage_project_id(pid), doc["id"])]
    row = store.get_document(doc["id"])
    assert os.path.exists(row["file_path"])  # the user keeps the file
    assert (row.get("metadata") or {}).get("indexing", {}).get("status") != "not_indexed"


def test_user_upload_with_a_project_is_queued_for_indexing(client, as_caller, indexed, monkeypatch):
    import app.routers.upload as upload_router

    queued: List[tuple] = []

    async def _enqueue(pid, did, job_id):
        queued.append((pid, did))
        return True

    monkeypatch.setattr(upload_router, "enqueue_ingest", _enqueue)
    as_caller(USER)
    pid = _project_owned_by(USER)

    r = client.post(
        "/upload",
        files={"file": ("rfi-log.txt", b"RFI 12 answered", "text/plain")},
        data={"project_id": pid},
        headers=NO_KEY,
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["document_id"]
    assert body["indexed"] is True
    assert body["indexing_status"] == "queued"
    assert queued == [(store.storage_project_id(pid), body["document_id"])]
    assert indexed == []


# ── rule 2: the admin path adds to the project RAG ────────────────────────


def test_admin_project_upload_is_indexed(client, as_caller, indexed):
    as_caller(ADMIN)
    pid = _project_owned_by(ADMIN)

    r = _post_document(client, pid)

    assert r.status_code == 201, r.text
    assert indexed == [(store.storage_project_id(pid), r.json()["document"]["id"])]


def test_admin_upload_with_a_project_is_queued_for_indexing(client, as_caller, monkeypatch):
    import app.routers.upload as upload_router

    queued: List[tuple] = []

    async def _enqueue(pid, did, job_id):
        queued.append((pid, did))
        return True

    monkeypatch.setattr(upload_router, "enqueue_ingest", _enqueue)
    as_caller(ADMIN)
    pid = _project_owned_by(ADMIN)

    r = client.post(
        "/upload",
        files={"file": ("spec.txt", b"concrete grade C40", "text/plain")},
        data={"project_id": pid},
        headers=NO_KEY,
    )

    assert r.status_code == 200, r.text
    assert r.json()["indexing_status"] == "queued"
    assert queued == [(store.storage_project_id(pid), r.json()["document_id"])]


# ── rule 3: no upload writes the user layer implicitly ────────────────────


def test_layered_retrieval_on_still_indexes_a_user_upload(client, as_caller, indexed, monkeypatch):
    """With RAG_LAYERED on, the upload is still scheduled. Classification
    (user_session vs an ordinary project chunk) happens inside the indexer
    and is not a reason to skip it."""
    monkeypatch.setenv("RAG_LAYERED", "1")
    as_caller(USER)
    pid = _project_owned_by(USER)

    r = _post_document(client, pid)

    assert r.status_code == 201, r.text
    assert indexed == [(store.storage_project_id(pid), r.json()["document"]["id"])]


# ── routes whose only job is adding to a project's RAG ────────────────────


@pytest.mark.parametrize("path,body", [
    ("drive/index-folder", {"folder_id": "folder-1"}),
    ("drive/import", {"file_id": "file-1", "name": "spec.pdf"}),
    ("connectors/aconex/sync", {}),
    ("connectors/aconex/events", {"ingest_documents": True}),
])
def test_non_admin_cannot_add_through_drive_or_aconex(client, as_caller, indexed, path, body):
    as_caller(USER)
    pid = _project_owned_by(USER)

    r = client.post(f"/v1/projects/{pid}/{path}", json=body, headers=NO_KEY)

    assert r.status_code == 403, r.text
    assert r.json()["detail"] == privileges.PROJECT_RAG_ADMIN_ONLY_DETAIL
    assert indexed == []


@pytest.mark.parametrize("path,body", [
    ("drive/index-folder", {"folder_id": "folder-1"}),
    ("drive/import", {"file_id": "file-1", "name": "spec.pdf"}),
])
def test_admin_passes_the_rule_on_drive_routes(client, as_caller, path, body):
    """Past the rule, an admin meets the next check (no Drive connected: 409)."""
    as_caller(ADMIN)
    pid = _project_owned_by(ADMIN)

    r = client.post(f"/v1/projects/{pid}/{path}", json=body, headers=NO_KEY)

    assert r.status_code == 409, r.text


# ── BOQ exports: the download stays; adding it to the RAG is admin-only ───


@pytest.mark.parametrize("principal,expect_added", [(USER, 0), (ADMIN, 1)])
def test_boq_export_adds_to_project_rag_only_for_admin(client, as_caller, indexed, principal, expect_added):
    as_caller(principal)
    pid = _project_owned_by(principal)
    before = {d["id"] for d in store.list_documents(pid)}

    r = client.post(
        f"/v1/projects/{pid}/export/cost-boq",
        json={"categories": SAMPLE_CATEGORIES},
        headers=NO_KEY,
    )

    assert r.status_code == 200, r.text
    assert r.content[:2] == b"PK"  # the download is served either way
    added = [d for d in store.list_documents(pid) if d["id"] not in before]
    assert len(added) == expect_added
    assert len(indexed) == expect_added


# ── Aconex through /v1/execute: rows cached, indexed only for an admin ────


@pytest.mark.parametrize("role,expect_eager", [(None, False), ("user", False), ("admin", True)])
def test_aconex_block_sync_indexes_only_for_admin(monkeypatch, role, expect_eager):
    import app.core.cde as cde
    from app.blocks.aconex import AconexBlock

    seen: List[bool] = []

    async def _sync(fork_project_id, cde_project_id, *, eager_index=True):
        seen.append(eager_index)
        return {"stored": 0, "documents": []}

    monkeypatch.setattr(cde, "sync_cde_documents", _sync)
    block = AconexBlock()
    privileges.set_caller_role(role)
    try:
        asyncio.run(block.execute(
            {"project_id": "p1", "cde_project_id": "cde-1"},
            {"operation": "sync", "project_id": "p1", "cde_project_id": "cde-1"},
        ))
    finally:
        privileges.set_caller_role(None)
    assert seen == [expect_eager]
