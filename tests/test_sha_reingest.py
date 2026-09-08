"""R3: ingest refuses a duplicate sha256 unless --reingest <old_id>."""
from __future__ import annotations

import hashlib
import importlib

import pytest

from app.core.projects import DuplicateContentError


def _reload(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import app.core.db as db_mod
    import app.core.users as users_mod
    from app.core import projects

    importlib.reload(db_mod)
    importlib.reload(users_mod)
    users_mod._initialized = False
    projects._initialized = False
    return importlib.reload(projects), users_mod


def _project(projects, users):
    users.ensure_user_exists("u1")
    projects.create_project(name="P", client="C", user_id="u1")
    return projects.list_projects("u1")[0]


def test_add_document_refuses_duplicate_sha_without_flag(monkeypatch, tmp_path):
    projects, users = _reload(monkeypatch, tmp_path)
    projects.init_db()
    proj = _project(projects, users)
    sha = hashlib.sha256(b"same-bytes-letter").hexdigest()
    first = projects.add_document(
        project_id=proj["id"],
        original_name="a.docx",
        stored_as="a.docx",
        file_path="/tmp/a.docx",
        size=139671,
        content_sha256=sha,
    )
    with pytest.raises(DuplicateContentError) as exc:
        projects.add_document(
            project_id=proj["id"],
            original_name="b.docx",
            stored_as="b.docx",
            file_path="/tmp/b.docx",
            size=139671,
            content_sha256=sha,
        )
    assert exc.value.existing_id == first["id"]
    assert len(projects.list_documents(proj["id"])) == 1


def test_reingest_flag_supersedes_old_row(monkeypatch, tmp_path):
    projects, users = _reload(monkeypatch, tmp_path)
    projects.init_db()
    proj = _project(projects, users)
    sha = hashlib.sha256(b"same-bytes-letter").hexdigest()
    old = projects.add_document(
        project_id=proj["id"],
        original_name="old.docx",
        stored_as="old.docx",
        file_path="/tmp/old.docx",
        size=139671,
        content_sha256=sha,
    )
    new = projects.add_document(
        project_id=proj["id"],
        original_name="new.docx",
        stored_as="new.docx",
        file_path="/tmp/new.docx",
        size=139671,
        content_sha256=sha,
        reingest_of=old["id"],
    )
    old_after = projects.get_document(old["id"])
    assert old_after is not None, "hard delete is forbidden"
    assert old_after["superseded_by"] == new["id"]
    assert old_after["retrieval_visible"] is False
    assert new["retrieval_visible"] is True
    assert new["id"] != old["id"]
    assert len(projects.list_documents(proj["id"])) == 2


def test_p1b_ingest_file_refuses_duplicate_sha(monkeypatch, tmp_path):
    """_ingest_file returns DUPLICATE_SHA when sha exists and no --reingest."""
    from scripts.p1b_ingest_drive_server import _ingest_file

    projects, users = _reload(monkeypatch, tmp_path)
    projects.init_db()
    proj = _project(projects, users)
    body = b"letter-bytes-for-sha"
    sha = hashlib.sha256(body).hexdigest()
    projects.add_document(
        project_id=proj["id"],
        original_name="letter.docx",
        stored_as="letter.docx",
        file_path=str(tmp_path / "letter.docx"),
        size=len(body),
        content_sha256=sha,
    )

    class _Drive:
        def download_file_bytes(self, _fid):
            return body, None

    monkeypatch.setattr(
        "app.core.r2_storage.archive_document",
        lambda **_k: {"archived": False, "r2_object_key": None},
    )
    monkeypatch.setattr("app.core.r2_storage.delete_local_archive", lambda *_a: None)
    monkeypatch.setattr("app.core.file_crypto.write_document", lambda *_a, **_k: None)

    _rel, result = _ingest_file(
        {"id": "drive1", "name": "letter.docx", "mimeType": "x", "size": str(len(body))},
        proj["id"],
        tmp_path,
        "run1",
        _Drive(),
    )
    assert result["error"] == "DUPLICATE_SHA"
    assert result["existing_id"]
    assert len(projects.list_documents(proj["id"])) == 1


def test_seed_d1_is_noop_when_ids_absent(monkeypatch, tmp_path):
    projects, _users = _reload(monkeypatch, tmp_path)
    projects.init_db()
    report = projects.seed_d1_letter_supersede()
    assert report["applied"] is False
    assert report["stale_present"] is False
    assert report["live_present"] is False
