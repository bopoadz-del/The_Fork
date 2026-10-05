"""HTTP search endpoint + eager background indexing — Stream C, Phase C3.

Tests:
  - Search returns ranked results (200)
  - 404 for missing project
  - 404 for cross-tenant access
  - 400 for empty query
  - 401 / 403 for unauthenticated access
  - skipped_unsupported count is reported correctly
  - Eager indexing: index built after upload when INDEX_ON_UPLOAD is true
  - Eager indexing disabled: index NOT built when INDEX_ON_UPLOAD=false
"""

import uuid
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.doc_index import _load_index
from tests._real_embedder import use_real_embedder

# Run-unique suffix so parallel test runs don't collide on user emails.
_RUN = uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _user_token(client, email):
    """Register + login a user; return the JWT token string."""
    client.post("/v1/users/register",
                json={"email": email, "password": "password12"})
    r = client.post("/v1/users/login",
                    json={"email": email, "password": "password12"})
    return r.json()["token"]


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _create_project(client, headers, name="Search Test Project"):
    r = client.post("/v1/projects", json={"name": name}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _upload_txt(client, headers, pid, filename, content_bytes):
    """Upload a .txt document to a project; return the document response dict."""
    files = {"file": (filename, content_bytes, "text/plain")}
    r = client.post(f"/v1/projects/{pid}/documents", files=files, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["document"]


# ── Task 1 tests ─────────────────────────────────────────────────────────────

def test_search_returns_ranked_results(client, monkeypatch):
    """Two docs with disjoint content: search for a term from one ranks it first.

    The fixture is two synthetic uploads, so it is isolated from the two
    things that made this an xfail(strict=False) coin toss (XPASS on the
    SQLite jobs, XFAIL on PostgreSQL):
      * the curated general-knowledge layer (construction_kb.md and friends,
        seeded at app start), whose lexical bonus filled the top 5 for this
        concrete-heavy query -- GK merging is switched off for this search;
      * CI's fake embedder, whose hash-of-text vectors make cosine order
        noise -- the real model is used (see tests/_real_embedder.py).
    """
    monkeypatch.setenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", "")
    use_real_embedder(monkeypatch)
    tok = _user_token(client, f"search-ranked-{_RUN}@x.com")
    h = _headers(tok)
    pid = _create_project(client, h, "Ranked Results Project")

    _upload_txt(client, h, pid, "concrete.txt",
                b"Concrete curing schedule: Portland cement mix ratio water-cement ratio slump test.")
    _upload_txt(client, h, pid, "electrical.txt",
                b"Electrical wiring conduit inspection cable grounding circuit breakers.")

    r = client.get(f"/v1/projects/{pid}/documents/search",
                   params={"q": "concrete curing Portland cement"}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["project_id"] == pid
    assert body["query"] == "concrete curing Portland cement"
    assert body["count"] == len(body["results"])
    # Only the project's own two uploads can be returned, and the
    # concrete-heavy query must rank concrete.txt first.
    filenames = [r["filename"] for r in body["results"]]
    assert filenames, body
    assert filenames[0] == "concrete.txt", filenames
    assert set(filenames) <= {"concrete.txt", "electrical.txt"}, filenames
    # Result shape
    for result in body["results"]:
        assert "document_id" in result
        assert "filename" in result
        assert "snippet" in result
        assert "score" in result


def test_search_404_for_missing_project(client):
    """Searching a nonexistent project_id returns 404."""
    tok = _user_token(client, f"search-404-missing-{_RUN}@x.com")
    h = _headers(tok)
    r = client.get("/v1/projects/nonexistent-project-id-xyz/documents/search",
                   params={"q": "anything"}, headers=h)
    assert r.status_code == 404


def test_search_404_cross_tenant(client):
    """User B cannot search User A's project — gets 404, not 403."""
    tok_a = _user_token(client, f"search-xtenant-a-{_RUN}@x.com")
    tok_b = _user_token(client, f"search-xtenant-b-{_RUN}@x.com")
    h_a = _headers(tok_a)
    h_b = _headers(tok_b)

    pid = _create_project(client, h_a, "Alice Secret Project")
    _upload_txt(client, h_a, pid, "secret.txt", b"Alice's confidential content here.")

    r = client.get(f"/v1/projects/{pid}/documents/search",
                   params={"q": "confidential"}, headers=h_b)
    assert r.status_code == 404


def test_search_400_empty_query(client):
    """Empty or whitespace-only query returns 400."""
    tok = _user_token(client, f"search-400-{_RUN}@x.com")
    h = _headers(tok)
    pid = _create_project(client, h, "Empty Query Project")

    # Empty string
    r = client.get(f"/v1/projects/{pid}/documents/search",
                   params={"q": ""}, headers=h)
    assert r.status_code == 400

    # Whitespace only
    r = client.get(f"/v1/projects/{pid}/documents/search",
                   params={"q": "   "}, headers=h)
    assert r.status_code == 400


def test_search_401_no_auth(client):
    """No Authorization header → 401 or 403."""
    r = client.get("/v1/projects/any-project-id/documents/search",
                   params={"q": "test"})
    assert r.status_code in (401, 403)


def test_search_reports_skipped(client):
    """A non-text file never becomes a project document (text formats only,
    docs/INGEST_EXCLUSION_RULE.md): the upload is refused, so the index holds
    nothing to skip and search reports skipped_unsupported == 0."""
    tok = _user_token(client, f"search-skipped-{_RUN}@x.com")
    h = _headers(tok)
    pid = _create_project(client, h, "Skipped Unsupported Project")

    _upload_txt(client, h, pid, "notes.txt",
                b"Project notes for the skipped unsupported test document content.")

    files = {"file": ("model.dwg", b"AutoCAD DWG binary" + b"\x00" * 20,
                      "application/octet-stream")}
    r = client.post(f"/v1/projects/{pid}/documents", files=files, headers=h)
    assert r.status_code == 415, r.text

    r = client.get(f"/v1/projects/{pid}/documents/search",
                   params={"q": "notes project"}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["skipped_unsupported"] == 0

    # A pre-rule non-text row still in the project is reported as skipped.
    import os

    from app.core import doc_index, file_crypto, projects as store

    legacy = os.path.join(os.environ.get("DATA_DIR", "."), f"legacy-{_RUN}.dwg")
    file_crypto.write_document(legacy, b"AutoCAD DWG binary")
    store.add_document(pid, "legacy.dwg", file_path=legacy, size=18)
    doc_index.index_project(pid)
    r = client.get(f"/v1/projects/{pid}/documents/search",
                   params={"q": "notes project"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["skipped_unsupported"] >= 1


# ── Task 2 tests ─────────────────────────────────────────────────────────────

# Legacy API key — avoids needing a JWT/user for background-indexing tests,
# and sidesteps the DB-re-init complexity when DATA_DIR is monkeypatched.
_LEGACY_H = {"Authorization": "Bearer cb_dev_key"}


@pytest.mark.usefixtures("dev_key_is_admin")
def test_upload_eager_indexes(tmp_path, monkeypatch):
    """With INDEX_ON_UPLOAD unset (defaults to true), uploading a doc builds the index."""
    import os
    from app.core import projects as projects_mod
    from app.core import users as users_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("INDEX_ON_UPLOAD", raising=False)
    # Re-init both DBs in the new DATA_DIR so all DB operations work.
    monkeypatch.setattr(projects_mod, "_initialized", False)
    monkeypatch.setattr(users_mod, "_initialized", False)

    with TestClient(app) as c:
        pid = _create_project(c, _LEGACY_H, "Eager Index Project")
        _upload_txt(c, _LEGACY_H, pid, "spec.txt",
                    b"Specification document content for eager indexing test.")

    # TestClient runs BackgroundTasks synchronously after the response,
    # so the index must exist immediately after the upload returns.
    assert _load_index(pid) is not None, "Index not built after eager indexing"


def test_eager_index_disabled(tmp_path, monkeypatch):
    """With INDEX_ON_UPLOAD=false, uploading a doc does NOT build the index."""
    import os
    from app.core import projects as projects_mod
    from app.core import users as users_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("INDEX_ON_UPLOAD", "false")
    # Re-init both DBs in the new DATA_DIR.
    monkeypatch.setattr(projects_mod, "_initialized", False)
    monkeypatch.setattr(users_mod, "_initialized", False)

    with TestClient(app) as c:
        pid = _create_project(c, _LEGACY_H, "No Eager Index Project")
        _upload_txt(c, _LEGACY_H, pid, "spec.txt",
                    b"Specification document content for disabled eager indexing test.")

    assert _load_index(pid) is None, \
        "Index should NOT exist when INDEX_ON_UPLOAD=false"
