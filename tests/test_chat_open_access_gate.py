"""Chat tenant gate follows the open-access rule (2026-07-26, third asymmetry).

#267 let admins/shared-users OPEN a project, #277 let them UPLOAD to it —
but the chat gates still did owner-only lookups and silently dropped the
project_id: zero injected RAG context and a no-op search tool on exactly the
projects the pilot shares (found live: the corpus project, owned by a seed
account, was invisible to every admin's chat). One rule now covers all
surfaces: owner -> admin-approved shared -> admin role. Strangers on private
projects stay dropped (fail-closed tenancy).
"""
import pytest

from app.core import projects as store
from app.core import users as users_store


def _mk_user(email, role="user"):
    try:
        u = users_store.create_user(email, "pw123456", role=role)
        return u["id"]
    except ValueError:
        u = users_store.get_user_by_email(email)
        if role != "user":
            from app.core.db import SessionLocal
            from app.core.models import User
            with SessionLocal() as db:
                db.get(User, u["id"]).role = role
                db.commit()
        return u["id"]


@pytest.fixture(scope="module")
def world():
    store.init_db()
    owner = _mk_user("gate-owner@example.com")
    admin = _mk_user("gate-admin@example.com", role="admin")
    stranger = _mk_user("gate-stranger@example.com")

    from app.core.db import SessionLocal
    from app.core.models import Project

    def _ensure(pid, name, shared=False):
        with SessionLocal() as db:
            row = db.get(Project, pid)
            if row is None:
                db.close()
                store.create_project(name, user_id=owner, project_id=pid)
                with SessionLocal() as db2:
                    row2 = db2.get(Project, pid)
                    row2.origin = "admin_drive_approved" if shared else "user_create"
                    row2.is_approved = bool(shared)
                    db2.commit()
            else:
                row.status = "active"
                row.user_id = owner
                row.origin = "admin_drive_approved" if shared else "user_create"
                row.is_approved = bool(shared)
                db.commit()

    _ensure("p_gate_priv", "Gate private")
    _ensure("p_gate_shared", "Gate shared", shared=True)
    yield {"owner": owner, "admin": admin, "stranger": stranger}
    for pid in ("p_gate_priv", "p_gate_shared", "p_gate_arch"):
        store.archive_project(pid)


def test_owner_resolves_both(world):
    assert store.get_project_accessible("p_gate_priv", world["owner"]) is not None
    assert store.get_project_accessible("p_gate_shared", world["owner"]) is not None


def test_admin_resolves_other_users_private_project(world):
    # THE live bug: chat dropped the corpus project for admins.
    proj = store.get_project_accessible("p_gate_priv", world["admin"])
    assert proj is not None and proj["id"] == "p_gate_priv"


def test_regular_user_resolves_shared_but_not_private(world):
    assert store.get_project_accessible("p_gate_shared", world["stranger"]) is not None
    assert store.get_project_accessible("p_gate_priv", world["stranger"]) is None


def test_anonymous_and_unknown_fail_closed(world):
    assert store.get_project_accessible("p_gate_priv", None) is None
    assert store.get_project_accessible("p_gate_priv", "no-such-user") is None
    assert store.get_project_accessible("nope_gate", world["admin"]) is None


def test_archived_projects_stay_invisible(world):
    from app.core.db import SessionLocal
    from app.core.models import Project
    with SessionLocal() as db:
        if db.get(Project, "p_gate_arch") is None:
            db.close()
            store.create_project("Gate archived", user_id=world["owner"], project_id="p_gate_arch")
    store.archive_project("p_gate_arch")
    # Even the admin path must not resurface an archived project in chat.
    assert store.get_project_accessible("p_gate_arch", world["admin"]) is None
