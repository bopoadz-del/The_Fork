"""FK constraint tests for the SQLAlchemy users store (Phase 1.3a)."""
import importlib

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def users_db(monkeypatch, tmp_path):
    """Fresh unified DB in a tmp dir with users schema initialized."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import app.core.db as db_mod
    import app.core.users as users_mod

    importlib.reload(db_mod)
    importlib.reload(users_mod)
    users_mod._initialized = False
    users_mod.init_db()
    return users_mod, db_mod


def test_delete_user_with_projects_blocked(users_db):
    """projects.user_id FK must block deleting a user who owns projects."""
    users_mod, db_mod = users_db
    owner = users_mod.create_user("owner@example.com", "secret-pw")
    uid = owner["id"]

    with db_mod.SessionLocal() as session:
        session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id               TEXT PRIMARY KEY,
                    name             TEXT NOT NULL,
                    client           TEXT,
                    status           TEXT NOT NULL DEFAULT 'active',
                    aconex_connected INTEGER NOT NULL DEFAULT 0,
                    user_id          TEXT NOT NULL
                                       REFERENCES users (id) ON DELETE RESTRICT,
                    created_at       TEXT NOT NULL
                )
                """
            )
        )
        session.execute(
            text(
                "INSERT INTO projects (id, name, user_id, created_at) "
                "VALUES (:id, :name, :uid, :ts)"
            ),
            {
                "id": "proj-fk-test",
                "name": "FK Test Project",
                "uid": uid,
                "ts": "2026-01-01T00:00:00+00:00",
            },
        )
        session.commit()

    with db_mod.SessionLocal() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                text("DELETE FROM users WHERE id = :uid"), {"uid": uid}
            )
            session.commit()
