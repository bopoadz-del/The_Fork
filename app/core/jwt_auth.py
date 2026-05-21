"""JWT session tokens — Stream A (User Accounts & Multi-Tenancy).

Pure-Python (PyJWT). SECRET_KEY comes from the env var of the same name;
if unset, a key is generated and persisted to {DATA_DIR}/.secret_key so
tokens survive process restarts. DATA_DIR is read at call time so tests
can relocate it.
"""
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone

import jwt
from jwt import InvalidTokenError  # re-exported for callers

_ALGORITHM = "HS256"
_lock = threading.Lock()
_cached_secret: str | None = None


def _default_expiry() -> int:
    return int(os.getenv("JWT_EXPIRY_SECONDS", "86400"))


def _secret_file() -> str:
    data_dir = os.getenv("DATA_DIR", "./data")
    try:
        os.makedirs(data_dir, exist_ok=True)
    except OSError:
        import tempfile
        data_dir = tempfile.gettempdir()
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
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                _cached_secret = f.read().strip()
        else:
            _cached_secret = secrets.token_hex(32)
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(_cached_secret)
            except OSError:
                pass  # fall back to in-memory secret for this process
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
