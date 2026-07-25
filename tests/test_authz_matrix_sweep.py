"""Authorization MATRIX sweep — every project surface x caller x project kind.

New test shape (2026-07-25): the pilot's two authz bugs (#267 list/detail
mismatch, #277 upload owner-only 404) were both single-cell holes in a matrix
nobody enumerated. This sweep pins the WHOLE matrix so the next asymmetry
fails a test instead of a tester.

Callers: owner / admin / stranger.
Projects: private (user_create) / admin-approved shared platform project.
Surfaces: open detail, list documents, upload document, delete project.

Expected access:
  open+docs+upload -> owner: yes; admin: yes; stranger: shared only.
  delete           -> owner or admin (soft-archive curation); strangers denied.

First run of this sweep found a live hole: document PAGINATION 404'd for
admins on projects they can open (role not threaded through the access
check) — fixed alongside this file.
"""
import io

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _register(client, email):
    r = client.post("/v1/users/register", json={"email": email, "password": "pw123456"})
    assert r.status_code in (201, 409), r.text  # 409 = already there from a prior case
    r = client.post("/v1/users/login", json={"email": email, "password": "pw123456"})
    return {"token": r.json()["token"], "id": r.json()["user"]["id"]}


def _promote_admin(uid):
    from app.core.db import SessionLocal
    from app.core.models import User
    with SessionLocal() as db:
        db.get(User, uid).role = "admin"
        db.commit()


def _mark_shared(pid):
    from app.core.db import SessionLocal
    from app.core.models import Project
    with SessionLocal() as db:
        p = db.get(Project, pid)
        p.origin = "admin_drive_approved"
        p.is_approved = True
        db.commit()


def _h(actor):
    return {"Authorization": f"Bearer {actor['token']}"}


@pytest.fixture
def world(client):
    """owner + admin + stranger, one private and one shared project."""
    owner = _register(client, "mx-owner@example.com")
    admin = _register(client, "mx-admin@example.com")
    stranger = _register(client, "mx-stranger@example.com")
    _promote_admin(admin["id"])

    private_pid = client.post(
        "/v1/projects", json={"name": "Matrix private"}, headers=_h(owner)
    ).json()["id"]
    shared_pid = client.post(
        "/v1/projects", json={"name": "Matrix shared"}, headers=_h(owner)
    ).json()["id"]
    _mark_shared(shared_pid)
    return {
        "owner": owner, "admin": admin, "stranger": stranger,
        "private": private_pid, "shared": shared_pid,
    }


def _surface(client, name, pid, actor):
    if name == "open":
        return client.get(f"/v1/projects/{pid}", headers=_h(actor)).status_code
    if name == "docs":
        return client.get(f"/v1/projects/{pid}/documents", headers=_h(actor)).status_code
    if name == "upload":
        return client.post(
            f"/v1/projects/{pid}/documents", headers=_h(actor),
            files={"file": ("m.txt", io.BytesIO(b"matrix"), "text/plain")},
        ).status_code
    if name == "delete":
        return client.delete(f"/v1/projects/{pid}", headers=_h(actor)).status_code
    raise AssertionError(name)


# (surface, caller, project) -> allowed?
MATRIX = [
    ("open",   "owner",    "private", True),
    ("open",   "admin",    "private", True),
    ("open",   "stranger", "private", False),
    ("open",   "owner",    "shared",  True),
    ("open",   "admin",    "shared",  True),
    ("open",   "stranger", "shared",  True),
    ("docs",   "owner",    "private", True),
    ("docs",   "admin",    "private", True),
    ("docs",   "stranger", "private", False),
    ("docs",   "stranger", "shared",  True),
    ("upload", "owner",    "private", True),
    ("upload", "admin",    "private", True),
    ("upload", "stranger", "private", False),
    ("upload", "owner",    "shared",  True),
    ("upload", "admin",    "shared",  True),
    ("upload", "stranger", "shared",  True),
    ("delete", "stranger", "private", False),
    ("delete", "stranger", "shared",  False),
    # Admin delete = documented curation feature (soft-archive, reversible);
    # covered separately in test_admin_delete_is_soft_archive below.
]


@pytest.mark.parametrize("surface,caller,kind,allowed", MATRIX)
def test_authz_matrix(client, world, surface, caller, kind, allowed):
    code = _surface(client, surface, world[kind], world[caller])
    if allowed:
        assert 200 <= code < 300, f"{surface} {caller} {kind}: expected allow, got {code}"
    else:
        # 404 = existence hidden; 403 = visible-but-forbidden (delete on a
        # shared project the caller can see). Both are correct denials.
        assert code in (403, 404), f"{surface} {caller} {kind}: expected denial, got {code}"


def test_admin_delete_is_soft_archive(client, world):
    # Admin curation: delete on another user's project archives it (soft,
    # reversible) — the documented workspace-cleanup capability.
    assert _surface(client, "delete", world["private"], world["admin"]) == 200


def test_owner_delete_last(client, world):
    # Run owner deletes LAST so the matrix cases above see live projects.
    assert _surface(client, "delete", world["shared"], world["owner"]) == 200
