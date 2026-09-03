"""R2 archive failure must not abort TIER-1 ingest.

Production PutObject AccessDenied (runs da768576d477 / 549f02b012c5 at
2642/4609) used to kill the P1B file — and, if it leaked past
``archive_document``, the whole ``p1b_ingest_drive_server`` run — before the
Neon document row was written. Archive is best-effort: log, set
``r2_archived=False``, keep indexing.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List

from botocore.exceptions import ClientError

from app.core import r2_storage


def _access_denied(operation: str = "PutObject") -> ClientError:
    """The botocore error Cloudflare R2 returns for a denied PutObject."""
    return ClientError(
        {
            "Error": {
                "Code": "AccessDenied",
                "Message": "Access Denied",
            },
            "ResponseMetadata": {"HTTPStatusCode": 403},
        },
        operation,
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _r2_env(monkeypatch) -> None:
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "test-id")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://example.r2.cloudflarestorage.com")
    monkeypatch.setenv("R2_BUCKET_NAME", "test-bucket")
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct")


class _DeniedS3:
    def put_object(self, **kwargs):
        raise _access_denied()


class _OkThenDeniedS3:
    def __init__(self) -> None:
        self.calls = 0

    def put_object(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise _access_denied()
        return {"ETag": '"ok"'}


def test_archive_document_access_denied_returns_not_archived(monkeypatch):
    """PutObject AccessDenied is swallowed; archived=False, no raise."""
    _r2_env(monkeypatch)
    monkeypatch.setattr(r2_storage, "_client", lambda: _DeniedS3())

    payload = b"%PDF-1.4 denied"
    result = r2_storage.archive_document(
        "proj-1", "drive-1", "spec.pdf", payload, _sha(payload),
    )
    assert result["archived"] is False
    assert result["r2_object_key"] is None
    assert result["error"]
    assert "AccessDenied" in result["error"]
    assert "R2_UPLOAD_FAILED" in result["error"]


def test_archive_document_client_construction_failure_returns_not_archived(monkeypatch):
    """``_client()`` used to sit outside the try; a raise there aborted ingest."""
    _r2_env(monkeypatch)

    def _boom():
        raise _access_denied("CreateClient")

    monkeypatch.setattr(r2_storage, "_client", _boom)

    payload = b"hello"
    result = r2_storage.archive_document(
        "proj-1", "drive-1", "spec.pdf", payload, _sha(payload),
    )
    assert result["archived"] is False
    assert "AccessDenied" in (result["error"] or "")


def test_archive_document_second_file_still_runs_after_access_denied(monkeypatch):
    """A denied PutObject must not poison the next file's archive attempt."""
    _r2_env(monkeypatch)
    s3 = _OkThenDeniedS3()
    # First call denied, second succeeds — invert by swapping order in helper:
    # helper denies call 1 then succeeds. That's the sequence we want.
    monkeypatch.setattr(r2_storage, "_client", lambda: s3)

    first = r2_storage.archive_document(
        "proj-1", "drive-1", "a.pdf", b"aaaa", _sha(b"aaaa"),
    )
    second = r2_storage.archive_document(
        "proj-1", "drive-2", "b.pdf", b"bbbb", _sha(b"bbbb"),
    )
    assert first["archived"] is False
    assert "AccessDenied" in (first["error"] or "")
    assert second["archived"] is True
    assert second["error"] is None
    assert second["r2_object_key"]
    assert s3.calls == 2


def _install_ingest_fakes(monkeypatch, archive_impl, added: List[Dict[str, Any]], indexed: List[str]):
    monkeypatch.setattr("app.core.r2_storage.archive_document", archive_impl)
    monkeypatch.setattr(
        "app.core.file_crypto.write_document",
        lambda path, data: Path(path).write_bytes(data),
    )
    monkeypatch.setattr(
        "app.core.projects.add_document",
        lambda **kw: added.append(kw) or {"id": f"doc-{len(added)}"},
    )
    monkeypatch.setattr(
        "app.core.projects.update_document_metadata",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "app.core.doc_index.index_document",
        lambda pid, did: indexed.append(did) or {"status": "ok", "rag_indexed": 2},
    )
    monkeypatch.setattr("app.core.r2_storage.delete_local_archive", lambda p: None)


class _FakeDrive:
    def __init__(self, payload: bytes = b"%PDF-1.4 body") -> None:
        self.payload = payload

    def download_file_bytes(self, fid):
        return self.payload, None


def test_p1b_ingest_still_writes_neon_when_archive_raises_access_denied(tmp_path, monkeypatch):
    """If archive_document leaks ClientError, _ingest_file still add_document."""
    from scripts.p1b_ingest_drive_server import _ingest_file

    added: List[Dict[str, Any]] = []
    indexed: List[str] = []

    def _raise_denied(**kwargs):
        raise _access_denied()

    _install_ingest_fakes(monkeypatch, _raise_denied, added, indexed)

    rel, result = _ingest_file(
        {
            "id": "drive-denied",
            "name": "khor_waterproofing_spec.pdf",
            "_drive_path": "Tier1/khor_waterproofing_spec.pdf",
            "mimeType": "application/pdf",
            "size": 13,
        },
        "proj-neon",
        tmp_path,
        "run-denied",
        _FakeDrive(),
    )
    assert rel.endswith("khor_waterproofing_spec.pdf")
    assert added, "document must still land in Neon after R2 AccessDenied"
    assert added[0]["project_id"] == "proj-neon"
    assert added[0]["original_name"] == "khor_waterproofing_spec.pdf"
    meta = added[0]["metadata"]
    assert "AccessDenied" in (meta.get("r2_archive_error") or "")
    assert "r2_object_key" not in meta
    assert indexed == ["doc-1"]
    assert result.get("rag_indexed") == 2
    assert result["r2_archive"]["archived"] is False
    assert result.get("status") != "error"


def test_p1b_ingest_still_writes_neon_when_archive_returns_not_archived(tmp_path, monkeypatch):
    """The non-raising path (archived=False) already used by R2_NOT_CONFIGURED."""
    from scripts.p1b_ingest_drive_server import _ingest_file

    added: List[Dict[str, Any]] = []
    indexed: List[str] = []

    def _soft_fail(**kwargs):
        return {
            "archived": False,
            "r2_object_key": None,
            "r2_bucket": "test-bucket",
            "r2_endpoint": "https://example.r2.cloudflarestorage.com",
            "r2_account_id": "acct",
            "error": "R2_UPLOAD_FAILED: ClientError: An error occurred (AccessDenied) when calling the PutObject operation: Access Denied",
        }

    _install_ingest_fakes(monkeypatch, _soft_fail, added, indexed)

    _, result = _ingest_file(
        {
            "id": "drive-soft",
            "name": "boq.xlsx",
            "_drive_path": "Tier1/boq.xlsx",
            "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "size": 20,
        },
        "proj-neon",
        tmp_path,
        "run-soft",
        _FakeDrive(b"PK\x03\x04fake"),
    )
    assert added, "soft archive failure must still create the document row"
    assert indexed == ["doc-1"]
    assert result["r2_archive"]["archived"] is False
    assert result.get("rag_indexed") == 2


def test_p1b_ingest_continues_to_next_file_after_access_denied(tmp_path, monkeypatch):
    """Two-file run: first archive raises, second archives OK; both hit Neon."""
    from scripts.p1b_ingest_drive_server import _ingest_file

    added: List[Dict[str, Any]] = []
    indexed: List[str] = []
    calls = {"n": 0}

    def _archive(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _access_denied()
        return {
            "archived": True,
            "r2_object_key": "projects/proj-neon/drive/drive-ok/abcd.pdf",
            "r2_bucket": "test-bucket",
            "r2_endpoint": "https://example.r2.cloudflarestorage.com",
            "r2_account_id": "acct",
            "error": None,
        }

    _install_ingest_fakes(monkeypatch, _archive, added, indexed)

    files = [
        {
            "id": "drive-fail",
            "name": "denied.pdf",
            "_drive_path": "Tier1/denied.pdf",
            "mimeType": "application/pdf",
            "size": 10,
        },
        {
            "id": "drive-ok",
            "name": "ok.pdf",
            "_drive_path": "Tier1/ok.pdf",
            "mimeType": "application/pdf",
            "size": 10,
        },
    ]
    results = [
        _ingest_file(fm, "proj-neon", tmp_path, "run-two", _FakeDrive())
        for fm in files
    ]
    assert len(added) == 2
    assert indexed == ["doc-1", "doc-2"]
    assert results[0][1]["r2_archive"]["archived"] is False
    assert results[1][1]["r2_archive"]["archived"] is True
    assert results[1][1]["r2_archive"]["r2_object_key"]
    assert added[1]["metadata"].get("r2_object_key")
