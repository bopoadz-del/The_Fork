"""doc_index SQLite backend — legacy migration and concurrent-safe writes."""

import json
import threading
import time

from app.core import doc_index


def test_init_db_migrates_legacy_json_index(tmp_path, monkeypatch):
    """A pre-SQLite data/doc_index/<pid>.json file is imported into the DB."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    legacy_dir = tmp_path / "doc_index"
    legacy_dir.mkdir()
    legacy = {
        "project_id": "proj-legacy",
        "built_at": "2026-01-01T00:00:00Z",
        "documents": [
            {"document_id": "d1", "filename": "a.txt",
             "fingerprint": "f", "chunks": ["hello world"]}
        ],
        "skipped": [],
    }
    (legacy_dir / "proj-legacy.json").write_text(json.dumps(legacy), encoding="utf-8")

    doc_index.init_db()

    loaded = doc_index._load_index("proj-legacy")
    assert loaded is not None
    assert loaded["documents"][0]["chunks"] == ["hello world"]


def test_update_index_is_read_modify_write(tmp_path, monkeypatch):
    """Two sequential _update_index calls each preserve the other's entry."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    def _appender(doc_id):
        def _m(current):
            current = current or {"project_id": "p", "documents": [], "skipped": []}
            current["documents"].append({"document_id": doc_id, "chunks": []})
            return current
        return _m

    doc_index._update_index("p", _appender("d1"))
    doc_index._update_index("p", _appender("d2"))

    idx = doc_index._load_index("p")
    assert [d["document_id"] for d in idx["documents"]] == ["d1", "d2"]


def test_concurrent_updates_do_not_lose_entries(tmp_path, monkeypatch):
    """8 threads each append a document concurrently — none is lost.

    The mutator sleeps to widen the race window; the transactional
    read-modify-write must still serialise so every entry survives.
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    def _add(doc_id):
        def _m(current):
            current = current or {"project_id": "p2", "documents": [], "skipped": []}
            time.sleep(0.01)  # widen the load-modify-write window
            current["documents"].append({"document_id": doc_id})
            return current
        doc_index._update_index("p2", _m)

    threads = [threading.Thread(target=_add, args=(f"d{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    idx = doc_index._load_index("p2")
    got = sorted(d["document_id"] for d in idx["documents"])
    assert got == sorted(f"d{i}" for i in range(8)), got
