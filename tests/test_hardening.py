"""Regression tests for the High/Medium production-hardening fixes."""

import pytest


# ── file_crypto: decrypt fails loud on a key mismatch ───────────────────────────

def test_decrypt_with_wrong_key_raises_loudly(monkeypatch):
    """A real Fernet token that cannot be decrypted (rotated/wrong key) must
    raise DecryptionError, not silently return the ciphertext."""
    from cryptography.fernet import Fernet
    from app.core import file_crypto

    key_a = Fernet.generate_key()
    key_b = Fernet.generate_key()

    monkeypatch.setenv("DATA_ENCRYPTION_KEY", key_a.decode())
    token = file_crypto.encrypt_bytes(b"confidential contract")
    # Correct key still round-trips.
    assert file_crypto.decrypt_bytes(token) == b"confidential contract"

    # Wrong key: must fail loud rather than return ciphertext as "plaintext".
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", key_b.decode())
    with pytest.raises(file_crypto.DecryptionError):
        file_crypto.decrypt_bytes(token)


def test_legacy_plaintext_still_passes_through(monkeypatch):
    """A plaintext file is returned untouched even when a key is configured."""
    from cryptography.fernet import Fernet
    from app.core import file_crypto

    monkeypatch.setenv("DATA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    assert file_crypto.decrypt_bytes(b"%PDF-1.4 plain bytes") == b"%PDF-1.4 plain bytes"


# ── startup: production requires SECRET_KEY ─────────────────────────────────────

def test_production_startup_requires_secret_key(monkeypatch):
    from app.main import _validate_startup_env

    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        _validate_startup_env()

    # With SECRET_KEY set, startup validation passes.
    monkeypatch.setenv("SECRET_KEY", "a-real-secret")
    _validate_startup_env()


def test_non_production_skips_secret_key_check(monkeypatch):
    from app.main import _validate_startup_env

    monkeypatch.setenv("ENV", "testing")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    _validate_startup_env()  # must not raise outside production


# ── doc_types: registry mutation is admin-only ──────────────────────────────────

def test_doc_types_mutation_is_admin_only():
    """A non-admin user cannot add or delete entries in the global doc-type
    registry; reads stay open to any authenticated caller."""
    import uuid

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        email = f"dt-{uuid.uuid4().hex[:8]}@x.com"
        c.post("/v1/users/register", json={"email": email, "password": "password12"})
        token = c.post(
            "/v1/users/login", json={"email": email, "password": "password12"}
        ).json()["token"]
        user = {"Authorization": f"Bearer {token}"}

        assert c.post(
            "/v1/document-types", headers=user, json={"name": "Sneaky Type"}
        ).status_code == 403
        assert c.delete(
            "/v1/document-types/whatever", headers=user
        ).status_code == 403
        # Reads remain available to any authenticated user.
        assert c.get("/v1/document-types", headers=user).status_code == 200


# ── /execute: code-execution blocks are admin-only ──────────────────────────────

def test_code_blocks_are_admin_only_via_execute():
    """A non-admin user cannot run the arbitrary-code blocks through /execute,
    but can still run ordinary blocks."""
    import uuid

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        email = f"ex-{uuid.uuid4().hex[:8]}@x.com"
        c.post("/v1/users/register", json={"email": email, "password": "password12"})
        token = c.post(
            "/v1/users/login", json={"email": email, "password": "password12"}
        ).json()["token"]
        user = {"Authorization": f"Bearer {token}"}

        # `code` runs arbitrary Python — the registered code-execution block.
        r = c.post("/v1/execute", headers=user, json={"block": "code", "input": "x"})
        assert r.status_code == 403, (r.status_code, r.text)

        # An ordinary block is still runnable by the same non-admin user.
        ok = c.post(
            "/v1/execute", headers=user,
            json={"block": "vector_search", "input": "x",
                  "params": {"operation": "list_collections"}},
        )
        assert ok.status_code != 403, ok.text


# ── Drive: OAuth token is per-user, not process-global ──────────────────────────

def test_drive_token_is_per_user(tmp_path, monkeypatch):
    """One user connecting Google Drive must not expose it to another user."""
    import time
    import uuid

    from fastapi.testclient import TestClient

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")

    from app.main import app
    from app.core import drive_auth

    with TestClient(app) as c:
        email = f"drv-{uuid.uuid4().hex[:8]}@x.com"
        c.post("/v1/users/register", json={"email": email, "password": "password12"})
        token = c.post(
            "/v1/users/login", json={"email": email, "password": "password12"}
        ).json()["token"]
        user_b = {"Authorization": f"Bearer {token}"}

        # The 'system' user (legacy cb_dev_key) connects Drive.
        drive_auth.save_token("system", {
            "access_token": "AT", "refresh_token": "RT",
            "expiry": time.time() + 9999, "email": "system@x.com",
        })

        # System sees it connected; user B must not.
        sys_status = c.get(
            "/v1/drive/status", headers={"Authorization": "Bearer cb_dev_key"}
        ).json()
        assert sys_status["connected"] is True

        b_status = c.get("/v1/drive/status", headers=user_b).json()
        assert b_status["connected"] is False

        # User B cannot list the system user's Drive files.
        assert c.get("/v1/drive/files", headers=user_b).status_code == 409
