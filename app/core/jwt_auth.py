"""JWT session tokens — Stream A (User Accounts & Multi-Tenancy).

Pure-Python (PyJWT). SECRET_KEY comes from the env var of the same name;
if unset, a key is generated and persisted to {DATA_DIR}/.secret_key so
tokens survive process restarts. DATA_DIR is read at call time so tests
can relocate it.
"""
import logging
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone

import jwt
from jwt import InvalidTokenError  # re-exported for callers

_ALGORITHM = "HS256"
_lock = threading.Lock()
_cached_secret: str | None = None

logger = logging.getLogger(__name__)


def _default_expiry() -> int:
    return int(os.getenv("JWT_EXPIRY_SECONDS", "86400"))


def _secret_file() -> str:
    data_dir = os.getenv("DATA_DIR", "./data")
    try:
        os.makedirs(data_dir, exist_ok=True)
    except OSError:
        import tempfile
        data_dir = tempfile.gettempdir()
        logger.warning(
            "Could not create DATA_DIR %r; signing secret will be stored in "
            "temp dir %r — rotate the secret if this host is shared.",
            os.getenv("DATA_DIR", "./data"),
            data_dir,
        )
    return os.path.join(data_dir, ".secret_key")


def _get_secret() -> str:
    """Resolve the signing secret: env var, else persisted file, else generate."""
    global _cached_secret
    if _cached_secret is not None:
        return _cached_secret
    with _lock:
        if _cached_secret is not None:
            return _cached_secret
        env_secret = os.getenv("SECRET_KEY")
        if env_secret:
            _cached_secret = env_secret
            return _cached_secret
        path = _secret_file()
        disk_secret: str | None = None
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                disk_secret = f.read().strip() or None  # treat empty file as missing
        if disk_secret:
            _cached_secret = disk_secret
        else:
            _cached_secret = secrets.token_hex(32)
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(_cached_secret)
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    # No-op on platforms without POSIX permissions (Windows),
                    # so this is EXPECTED there and must not be a warning — it
                    # would fire on every boot. It still gets recorded, because
                    # on a POSIX host it means the signing secret is sitting on
                    # disk more readable than intended.
                    logger.debug(
                        "could not chmod 0600 the JWT secret at %s "
                        "(expected on non-POSIX platforms)", path, exc_info=True,
                    )
            except OSError:
                # The secret could not be PERSISTED. This process falls back to
                # an in-memory secret, which means every token it issues becomes
                # invalid on restart, and a second worker will not accept them.
                # Genuinely worth a warning.
                logger.warning(
                    "could not persist the JWT secret to %s — falling back to "
                    "an in-memory secret; tokens will not survive a restart or "
                    "be valid across workers", path, exc_info=True,
                )
        return _cached_secret


def create_token(user_id: str, expires_in: int | None = None) -> str:
    exp_seconds = _default_expiry() if expires_in is None else expires_in
    payload = {
        "user_id": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=exp_seconds),
    }
    return jwt.encode(payload, _get_secret(), algorithm=_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode + verify a token. Raises jwt.InvalidTokenError on any failure."""
    return jwt.decode(token, _get_secret(), algorithms=[_ALGORITHM])


def signing_secret() -> str:
    """The app's signing secret, for other HMAC uses (e.g. OAuth state).

    Same resolution order as JWTs: SECRET_KEY env var, else the persisted
    DATA_DIR/.secret_key file — so values signed with it survive restarts
    and are shared by every worker on the same host/disk."""
    return _get_secret()
