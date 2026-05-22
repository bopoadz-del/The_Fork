"""Encryption at rest for uploaded documents — Roadmap V2 · Epic 6.

The Fork stores client documents in ``DATA_DIR`` as files. This module adds
optional symmetric encryption (Fernet / AES-128-CBC + HMAC) so those files are
ciphertext on disk.

Design — OPT-IN, transparent, backward-compatible
-------------------------------------------------
* The feature is driven entirely by the ``DATA_ENCRYPTION_KEY`` env var. It
  must hold a valid Fernet key (generate one with
  ``Fernet.generate_key()``). If the var is unset, encryption is OFF and every
  function behaves exactly as before — plaintext in, plaintext out — so the
  default test/dev experience is unchanged.
* Reading is backward-compatible: ``read_document`` / ``open_plaintext``
  inspect the bytes on disk and only decrypt files that actually look like a
  Fernet token. Pre-existing plaintext files in ``DATA_DIR`` keep working even
  after a key is configured.

Public API
----------
* ``encryption_enabled() -> bool``
* ``encrypt_bytes(data) -> bytes`` / ``decrypt_bytes(token) -> bytes``
* ``looks_encrypted(blob) -> bool``
* ``write_document(path, data)``        — encrypts iff enabled
* ``read_document(path) -> bytes``      — returns plaintext, decrypting iff needed
* ``open_plaintext(path)`` (context manager) — yields a real filesystem path
  containing plaintext (the original file when unencrypted, a secure temp copy
  when encrypted; the temp copy is removed on exit).
"""

import contextlib
import os
import tempfile
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

# Fernet tokens are URL-safe base64 and the decoded payload starts with the
# version byte 0x80 (per the Fernet spec). We use that to tell a real token
# apart from legacy plaintext files.
_FERNET_VERSION = 0x80

_ENV_KEY = "DATA_ENCRYPTION_KEY"


def _load_fernet() -> Optional[Fernet]:
    """Build a Fernet instance from the env var, or None if unset/invalid."""
    raw = os.getenv(_ENV_KEY)
    if not raw:
        return None
    try:
        return Fernet(raw.encode() if isinstance(raw, str) else raw)
    except (ValueError, TypeError) as exc:  # malformed key
        raise ValueError(
            f"{_ENV_KEY} is set but is not a valid Fernet key. Generate one "
            f"with cryptography.fernet.Fernet.generate_key(). ({exc})"
        ) from exc


def encryption_enabled() -> bool:
    """True when a valid DATA_ENCRYPTION_KEY is configured."""
    return _load_fernet() is not None


def looks_encrypted(blob: bytes) -> bool:
    """Heuristically detect whether ``blob`` is a Fernet token.

    A Fernet token is URL-safe base64; decoded it begins with the 0x80 version
    byte and is at least 57 bytes (version + 8B timestamp + 16B IV + 32B HMAC).
    Legacy plaintext documents (PDF, images, text, ...) will not decode cleanly
    to that shape, so this lets the reader transparently pass legacy files
    through. Any uncertainty errs on the side of "not encrypted".
    """
    if not blob:
        return False
    try:
        import base64

        decoded = base64.urlsafe_b64decode(blob)
    except Exception:
        return False
    return len(decoded) >= 57 and decoded[0] == _FERNET_VERSION


def encrypt_bytes(data: bytes) -> bytes:
    """Encrypt ``data``. Returns ``data`` unchanged when encryption is off."""
    fernet = _load_fernet()
    if fernet is None:
        return data
    return fernet.encrypt(data)


def decrypt_bytes(token: bytes) -> bytes:
    """Decrypt a Fernet token. Returns the input unchanged when encryption is
    off, or when the input is not actually a Fernet token (legacy plaintext)."""
    fernet = _load_fernet()
    if fernet is None:
        return token
    if not looks_encrypted(token):
        return token
    try:
        return fernet.decrypt(token)
    except InvalidToken:
        # Not our token after all (e.g. plaintext that happened to base64-decode
        # to a 0x80-prefixed blob, or a token from a different key). Treat it as
        # legacy plaintext rather than failing the read.
        return token


def write_document(path: str, data: bytes) -> None:
    """Write ``data`` to ``path``, encrypting it iff encryption is enabled."""
    payload = encrypt_bytes(data)
    with open(path, "wb") as fh:
        fh.write(payload)


def read_document(path: str) -> bytes:
    """Read ``path`` and return plaintext bytes.

    Transparently decrypts encrypted files and passes legacy plaintext files
    through untouched.
    """
    with open(path, "rb") as fh:
        raw = fh.read()
    return decrypt_bytes(raw)


@contextlib.contextmanager
def open_plaintext(path: str):
    """Yield a filesystem path that contains the document's plaintext.

    Libraries like PIL, Tesseract and PyMuPDF need a real file. When the file
    on disk is encrypted this decrypts it to a secure temp file and yields that
    path, removing the temp file on exit. When the file is plaintext (legacy
    files, or encryption disabled) it simply yields the original path — no copy
    is made.
    """
    with open(path, "rb") as fh:
        raw = fh.read()

    if not (encryption_enabled() and looks_encrypted(raw)):
        # Plaintext on disk — hand back the original path, nothing to clean up.
        yield path
        return

    plaintext = decrypt_bytes(raw)
    # Preserve the suffix so downstream code that sniffs by extension still works.
    suffix = os.path.splitext(path)[1] or ""
    fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="fork_dec_")
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(plaintext)
        yield tmp_path
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
