"""Migration test: legacy projects.db gains a user_id column. Stream A."""
import importlib
import sqlite3
import pytest
from app.core import projects as projects_mod


def test_legacy_db_is_migrated_and_backfilled(monkeypatch, tmp_path):
    db_path = tmp_path / "projects.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE projects (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, client TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            aconex_connected INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO projects (id, name, status, aconex_connected, created_at) "
        "VALUES ('legacy01', 'Old Project', 'active', 0, '2024-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    pm = importlib.reload(projects_mod)
    pm.init_db()

    conn = sqlite3.connect(db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()]
    assert "user_id" in cols
    row = conn.execute(
        "SELECT user_id FROM projects WHERE id = 'legacy01'"
    ).fetchone()
    conn.close()
    assert row[0] == "system"


def test_init_db_on_fresh_db_has_user_id(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    pm = importlib.reload(projects_mod)
    pm.init_db()
    conn = sqlite3.connect(tmp_path / "projects.db")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()]
    conn.close()
    assert "user_id" in cols
