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
