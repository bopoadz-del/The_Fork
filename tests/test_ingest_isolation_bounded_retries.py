"""One file can no longer take the ingest down, and no file is retried forever.

Live 2026-10-03: every pass of the Drive ingest reached file 723 of 1,048
and was OOM-killed on the same drawing PDF. Text extraction was already
isolated; the table stages ran in the parent. And open outcomes
(ZERO_CHUNK, EXTRACT_FAILED, UNVERIFIED) were retried on every pass with no
bound, so a deterministic failure repeated forever.

These tests pin the mechanism, not the file:
  * the whole chunk-producing stage of a file runs in its own child; a child
    that exhausts memory, is killed, or runs past its time leaves the parent
    running and the file EXTRACT_FAILED;
  * one bad file in a batch does not stop the others;
  * an open outcome is retried only up to INGEST_MAX_ATTEMPTS, then closed
    for this extractor, and resume skips it.
The fork-based tests need POSIX fork (CI is Linux); the retry tests run
everywhere.
"""
from __future__ import annotations

import importlib
import os
import signal
import time

import pytest

from tests.conftest import _postgres_test_mode

pytestmark = pytest.mark.skipif(
    _postgres_test_mode(), reason="SQLite-backed ingest tests",
)

needs_fork = pytest.mark.skipif(
    os.name != "posix", reason="per-file isolation needs POSIX fork",
)


def _reload(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    import app.core.db as db_mod
    import app.core.users as users_mod
    from app.core import projects
    from app.core.rag.embeddings import reset_embedder_cache
    from app.core.rag.vector_store import reset_store_cache

    importlib.reload(db_mod)
    importlib.reload(users_mod)
    users_mod._initialized = False
    projects._initialized = False
    reset_store_cache()
    reset_embedder_cache()
    import app.core.doc_index as doc_index_mod

    doc_index_mod = importlib.reload(doc_index_mod)
    projects = importlib.reload(projects)
    projects.init_db()
    users_mod.ensure_user_exists("u1")
    pid = projects.create_project(name="P", client="C", user_id="u1")["id"]
    return projects, doc_index_mod, pid


def _add_txt(projects, tmp_path, pid, name, body: bytes):
    from app.core import file_crypto

    path = str(tmp_path / name)
    file_crypto.write_document(path, body)
    return projects.add_document(
        pid, name, file_path=path, size=len(body),
        content_sha256=f"sha-{name}",
    )


def _isolate_everything(monkeypatch):
    """Isolation threshold to 0 so every file takes the child path."""
    from app.core import extract_isolated

    monkeypatch.setattr(extract_isolated, "_MIN_MB", 0.0)


# ── per-file isolation ─────────────────────────────────────────────────────


@needs_fork
def test_child_over_memory_limit_parent_continues_file_extract_failed(monkeypatch, tmp_path):
    projects, doc_index, pid = _reload(monkeypatch, tmp_path)
    _isolate_everything(monkeypatch)
    monkeypatch.setenv("DOC_INDEX_FILE_MEM_MB", "64")
    real = doc_index._produce_chunks

    def _greedy(*args):
        hoard = []
        while True:  # well past a 64 MB address-space budget
            hoard.append(bytearray(32 * 1024 * 1024))
        return real(*args)  # pragma: no cover

    monkeypatch.setattr(doc_index, "_produce_chunks", _greedy)
    doc = _add_txt(projects, tmp_path, pid, "a.txt", b"some words " * 50)

    result = doc_index.index_document(pid, doc["id"])

    assert result["error"] == "ZERO_CHUNK", result
    assert "memory" in (result.get("extract_error") or ""), result
    assert projects.get_document(doc["id"])["ingest_status"] == "EXTRACT_FAILED"


@needs_fork
def test_child_killed_parent_continues_file_extract_failed(monkeypatch, tmp_path):
    """The kernel OOM killer's SIGKILL reaches the child, not the parent."""
    projects, doc_index, pid = _reload(monkeypatch, tmp_path)
    _isolate_everything(monkeypatch)

    def _killed(*args):
        os.kill(os.getpid(), signal.SIGKILL)

    monkeypatch.setattr(doc_index, "_produce_chunks", _killed)
    doc = _add_txt(projects, tmp_path, pid, "a.txt", b"some words " * 50)

    result = doc_index.index_document(pid, doc["id"])

    assert result["error"] == "ZERO_CHUNK", result
    assert "crash" in (result.get("extract_error") or ""), result
    assert projects.get_document(doc["id"])["ingest_status"] == "EXTRACT_FAILED"


@needs_fork
def test_child_timeout_parent_continues_file_extract_failed(monkeypatch, tmp_path):
    projects, doc_index, pid = _reload(monkeypatch, tmp_path)
    _isolate_everything(monkeypatch)
    monkeypatch.setenv("DOC_INDEX_FILE_TIMEOUT_S", "1")

    def _wedged(*args):
        time.sleep(60)

    monkeypatch.setattr(doc_index, "_produce_chunks", _wedged)
    doc = _add_txt(projects, tmp_path, pid, "a.txt", b"some words " * 50)

    t0 = time.monotonic()
    result = doc_index.index_document(pid, doc["id"])

    assert time.monotonic() - t0 < 30, "parent waited out the wedged child"
    assert "timeout" in (result.get("extract_error") or ""), result
    assert projects.get_document(doc["id"])["ingest_status"] == "EXTRACT_FAILED"


@needs_fork
def test_one_bad_file_in_a_batch_the_others_are_processed(monkeypatch, tmp_path):
    projects, doc_index, pid = _reload(monkeypatch, tmp_path)
    _isolate_everything(monkeypatch)
    real = doc_index._produce_chunks
    marker = "POISON"

    def _one_bad(file_path, filename, *rest):
        if marker in filename:
            os.kill(os.getpid(), signal.SIGKILL)
        return real(file_path, filename, *rest)

    monkeypatch.setattr(doc_index, "_produce_chunks", _one_bad)
    docs = [
        _add_txt(projects, tmp_path, pid, f"{'POISON' if i == 2 else 'good'}{i}.txt",
                 f"retaining wall drainage clause {i} ".encode() * 40)
        for i in range(5)
    ]

    results = [doc_index.index_document(pid, d["id"]) for d in docs]

    good = [r for i, r in enumerate(results) if i != 2]
    assert all(r.get("status") == "ok" and r.get("total_chunks", 0) > 0 for r in good), good
    assert results[2]["error"] == "ZERO_CHUNK"
    statuses = [projects.get_document(d["id"])["ingest_status"] for d in docs]
    assert statuses[2] == "EXTRACT_FAILED"
    assert all(s != "EXTRACT_FAILED" for i, s in enumerate(statuses) if i != 2), statuses


def test_isolated_child_drops_inherited_db_pool_without_closing_it(monkeypatch):
    """A forked child must never share the parent's DB sockets."""
    from app.core import extract_isolated

    calls = []

    class _Engine:
        def dispose(self, close=True):
            calls.append(close)

    monkeypatch.setattr("app.core.db.get_engine", lambda: _Engine())
    extract_isolated._forget_inherited_db_pools()
    assert calls == [False]


# ── bounded retries ────────────────────────────────────────────────────────


def test_open_outcome_closes_at_max_attempts_and_resume_skips_it(monkeypatch, tmp_path):
    projects, doc_index, pid = _reload(monkeypatch, tmp_path)
    from app.core import ingest_status as ist

    monkeypatch.setenv("INGEST_MAX_ATTEMPTS", "2")
    monkeypatch.setattr(doc_index, "_produce_chunks", lambda *a: ([], {}))
    doc = _add_txt(projects, tmp_path, pid, "empty.txt", b"x")

    doc_index.index_document(pid, doc["id"])
    row = projects.get_document(doc["id"])
    assert row["ingest_status"] == ist.ZERO_CHUNK
    assert ist.is_open(row["ingest_status"], row["ingest_status_reason"])
    assert not ist.resume_is_already_indexed(row, 0)  # 1 of 2: retried

    doc_index.index_document(pid, doc["id"])
    row = projects.get_document(doc["id"])
    assert row["ingest_status"] == ist.ZERO_CHUNK
    assert ist.attempts_exhausted(row["ingest_status_reason"])
    assert not ist.is_open(row["ingest_status"], row["ingest_status_reason"])
    assert ist.resume_is_already_indexed(row, 0)  # 2 of 2: skipped


def test_bound_applies_uniformly_to_every_recoverable_class(monkeypatch):
    from app.core import ingest_status as ist

    for status, reason in (
        (ist.UNVERIFIED, None),
        (ist.ZERO_CHUNK, None),
        (ist.EXTRACT_FAILED, None),
        (ist.UNSUPPORTED_TYPE, "dwg:recoverable"),
        (ist.TEXT_SPARSE, "single_window:recoverable"),
    ):
        assert ist.is_open(status, reason), (status, reason)
        closed = ist.exhausted_reason(reason, 1)
        assert not ist.is_open(status, closed), (status, closed)


def test_a_killed_attempt_still_counts_toward_the_bound(monkeypatch):
    """No outcome was stamped (the process died) -- the count alone bounds it."""
    from app.core import ingest_status as ist

    monkeypatch.setenv("INGEST_MAX_ATTEMPTS", "1")
    doc = {
        "original_name": "a.pdf", "ingest_status": ist.UNVERIFIED,
        "content_sha256": "abc",
        "metadata": {"ingest_attempts": {"n": 1, "key": ist.attempt_key("abc")}},
    }
    assert ist.resume_is_already_indexed(doc, 0)


def test_new_bytes_or_new_extractor_reopen_an_exhausted_row(monkeypatch):
    from app.core import ingest_status as ist

    monkeypatch.setenv("INGEST_MAX_ATTEMPTS", "1")
    doc = {
        "original_name": "a.pdf", "ingest_status": ist.UNVERIFIED,
        "content_sha256": "abc",
        "metadata": {"ingest_attempts": {"n": 1, "key": ist.attempt_key("abc")}},
    }
    assert ist.resume_is_already_indexed(doc, 0)
    changed = {**doc, "content_sha256": "def"}
    assert not ist.resume_is_already_indexed(changed, 0)
    monkeypatch.setattr(ist, "EXTRACTOR_VERSION", "next-extractor")
    assert not ist.resume_is_already_indexed(doc, 0)
    assert ist.is_open(ist.ZERO_CHUNK, "x|attempts_exhausted@older:1:terminal")


def test_attempt_counter_restarts_on_a_new_key(monkeypatch, tmp_path):
    projects, _doc_index, pid = _reload(monkeypatch, tmp_path)
    doc = _add_txt(projects, tmp_path, pid, "a.txt", b"words")
    assert projects.record_ingest_attempt(doc["id"], "k1") == 1
    assert projects.record_ingest_attempt(doc["id"], "k1") == 2
    assert projects.record_ingest_attempt(doc["id"], "k2") == 1
    assert projects.ingest_attempts(projects.get_document(doc["id"]), "k2") == 1


def test_duplicate_content_source_is_recorded_once(monkeypatch, tmp_path):
    projects, _doc_index, pid = _reload(monkeypatch, tmp_path)
    doc = _add_txt(projects, tmp_path, pid, "a.txt", b"words")
    projects.record_source_alias(doc["id"], "src-2", "tok")
    projects.record_source_alias(doc["id"], "src-2", "tok")
    meta = projects.get_document(doc["id"])["metadata"]
    assert meta["source_aliases"] == {"src-2": "tok"}
