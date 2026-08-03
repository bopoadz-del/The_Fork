"""The handlers that used to swallow silently must now LOG — and still not raise.

Both properties matter and they pull against each other:

  * LOG, because silence is what hid these failures. `open_plaintext` leaving
    a DECRYPTED client document on disk, and the audit log failing to record
    that a document was touched, are exactly the events you need to know
    about — and both produced nothing at all.

  * DON'T RAISE, because these run in cleanup / bookkeeping positions. An
    upload must not fail because logging failed, and a `finally` that raises
    would mask the real error the caller is already reporting.

Covers the error paths added when the silent handlers were triaged
(repo audit 2026-08-03, finding #2).
"""
from __future__ import annotations

import logging

import pytest

from app.core import audit as audit_mod
from app.core import file_crypto


# ── audit log write failure ──────────────────────────────────────────────────

def test_audit_write_failure_is_logged_and_does_not_raise(monkeypatch, caplog):
    """A failed audit write must be loud. The trail is the product here."""
    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", _boom)

    with caplog.at_level(logging.WARNING, logger=audit_mod.__name__):
        entry = audit_mod.record("document.delete", document_id="d1")

    # caller still gets its entry back — bookkeeping never breaks the operation
    assert entry["event"] == "document.delete"
    assert entry["document_id"] == "d1"

    assert any("AUDIT WRITE FAILED" in r.message for r in caplog.records), caplog.text
    assert any("document.delete" in str(r.args) or "document.delete" in r.getMessage()
               for r in caplog.records), caplog.text


# ── decrypted temp-file cleanup failure ──────────────────────────────────────

def test_plaintext_temp_cleanup_failure_is_logged(monkeypatch, tmp_path, caplog):
    """If the decrypted temp file cannot be removed, say so.

    Silence here means plaintext left on disk — the exact state encryption at
    rest exists to prevent.
    """
    key = file_crypto.Fernet.generate_key().decode()
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", key)

    src = tmp_path / "doc.txt"
    src.write_bytes(file_crypto.encrypt_bytes(b"confidential contents"))

    import os as _os
    real_remove = _os.remove

    def _fail_remove(path, *a, **k):
        if "fork_dec_" in str(path):
            raise OSError("locked by another process")
        return real_remove(path, *a, **k)

    monkeypatch.setattr(file_crypto.os, "remove", _fail_remove)

    with caplog.at_level(logging.WARNING, logger=file_crypto.__name__):
        with file_crypto.open_plaintext(str(src)) as p:
            assert open(p, "rb").read() == b"confidential contents"

    assert any("decrypted temp file" in r.getMessage() for r in caplog.records), caplog.text
    assert any("plaintext may" in r.getMessage() for r in caplog.records), caplog.text


def test_cleanup_failure_does_not_propagate(monkeypatch, tmp_path):
    """The context manager must exit cleanly even when cleanup fails.

    A raising `finally` would replace whatever the caller was doing with an
    unlink error.
    """
    key = file_crypto.Fernet.generate_key().decode()
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", key)

    src = tmp_path / "doc.txt"
    src.write_bytes(file_crypto.encrypt_bytes(b"data"))

    def _always_fail(path, *a, **k):
        raise OSError("nope")

    monkeypatch.setattr(file_crypto.os, "remove", _always_fail)

    with file_crypto.open_plaintext(str(src)) as p:  # must not raise
        assert p


def test_plaintext_path_is_not_copied_or_cleaned(monkeypatch, tmp_path):
    """Unencrypted input yields the original path — nothing to clean up, so
    the cleanup handler must not run at all."""
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    src = tmp_path / "plain.txt"
    src.write_bytes(b"not encrypted")

    called = {"n": 0}
    monkeypatch.setattr(
        file_crypto.os, "remove",
        lambda *a, **k: called.__setitem__("n", called["n"] + 1),
    )

    with file_crypto.open_plaintext(str(src)) as p:
        assert p == str(src)

    assert called["n"] == 0
    assert src.exists()
