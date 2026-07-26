"""Chat tenant gate follows the open-access rule (2026-07-26, third asymmetry).

#267 let admins/shared-users OPEN a project, #277 let them UPLOAD to it —
but the chat gates still did owner-only lookups and silently dropped the
project_id: zero injected RAG context and a no-op search tool on exactly the
projects the pilot shares (found live: the corpus project, owned by a seed
account, was invisible to every admin's chat). One rule now covers all
surfaces: owner -> admin-approved shared -> admin role. Strangers on private
projects stay dropped (fail-closed tenancy).

World-building goes through the HTTP register/login API + minimal ORM
promotion — the same pattern as test_authz_matrix_sweep, which is green on
both SQLite and the shared-Postgres CI job (store-direct fixtures were not).
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core import projects as store


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _register(client, email):
    r = client.post("/v1/users/register", json={"email": email, "password": "pw123456"})
    assert r.status_code in (201, 409), r.text
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
    owner = _register(client, "oag-owner@example.com")
    admin = _register(client, "oag-admin@example.com")
    stranger = _register(client, "oag-stranger@example.com")
    _promote_admin(admin["id"])

    private_pid = client.post(
        "/v1/projects", json={"name": "OAG private"}, headers=_h(owner)
    ).json()["id"]
    shared_pid = client.post(
        "/v1/projects", json={"name": "OAG shared"}, headers=_h(owner)
    ).json()["id"]
    _mark_shared(shared_pid)
    yield {
        "owner": owner, "admin": admin, "stranger": stranger,
        "private": private_pid, "shared": shared_pid,
    }
    for pid in (private_pid, shared_pid):
        store.archive_project(pid)


def test_owner_resolves_both(world):
    assert store.get_project_accessible(world["private"], world["owner"]["id"]) is not None
    assert store.get_project_accessible(world["shared"], world["owner"]["id"]) is not None


def test_admin_resolves_other_users_private_project(world):
    # THE live bug: chat dropped the corpus project for admins.
    proj = store.get_project_accessible(world["private"], world["admin"]["id"])
    assert proj is not None and proj["id"] == world["private"]


def test_regular_user_resolves_shared_but_not_private(world):
    assert store.get_project_accessible(world["shared"], world["stranger"]["id"]) is not None
    assert store.get_project_accessible(world["private"], world["stranger"]["id"]) is None


def test_anonymous_and_unknown_fail_closed(world):
    assert store.get_project_accessible(world["private"], None) is None
    assert store.get_project_accessible(world["private"], "no-such-user") is None
    assert store.get_project_accessible("nope_gate", world["admin"]["id"]) is None


def test_archived_projects_stay_invisible(client, world):
    pid = client.post(
        "/v1/projects", json={"name": "OAG archived"}, headers=_h(world["owner"])
    ).json()["id"]
    store.archive_project(pid)
    # Even the admin path must not resurface an archived project in chat.
    assert store.get_project_accessible(pid, world["admin"]["id"]) is None
