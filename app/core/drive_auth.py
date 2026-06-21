"""Per-user Google Drive OAuth token store + refresh.

Each connected user gets their own token file
(DATA_DIR/google_drive_token_<user>.json). The token — including the refresh
token, a secret — is persisted via file_crypto, so it is encrypted at rest
when DATA_ENCRYPTION_KEY is set. Tokens are NOT shared between users.
"""
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from app.core import file_crypto

_TOKEN_URL = "https://oauth2.googleapis.com/token"


class DriveNotConnected(Exception):
    """No Google Drive token is stored for this user."""


class DriveAuthError(Exception):
    """Token exchange or refresh failed."""


def _safe_user(user_id: str) -> str:
    """Filesystem-safe form of a user id for use in the token filename."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", str(user_id or "")).strip("_")
    if not cleaned:
        raise DriveAuthError("A user id is required for the Drive token store.")
    return cleaned


def _token_path(user_id: str) -> Path:
    return (
        Path(os.getenv("DATA_DIR", "./data"))
        / f"google_drive_token_{_safe_user(user_id)}.json"
    )


def save_token(user_id: str, token: Dict[str, Any]) -> None:
    path = _token_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    file_crypto.write_document(str(path), json.dumps(token).encode("utf-8"))


def load_token(user_id: str) -> Optional[Dict[str, Any]]:
    path = _token_path(user_id)
    if not path.exists():
        return None
    return json.loads(file_crypto.read_document(str(path)).decode("utf-8"))


def clear_token(user_id: str) -> bool:
    path = _token_path(user_id)
    if path.exists():
        path.unlink()
        return True
    return False


async def _refresh_request(refresh_token: str) -> Dict[str, Any]:
    """POST the refresh grant to Google. Overridable seam for tests."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(_TOKEN_URL, data={
            "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", ""),
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        })
    if resp.status_code != 200:
        # Surface Google's actual reason — usually one of:
        #   invalid_grant + "Token has been expired or revoked"  → testing-mode
        #       7-day expiry, user revoked, password change, or 6mo unused
        #   invalid_client                                       → wrong creds
        # Helps the operator distinguish "publish OAuth consent screen" from
        # "rotate credentials" without log-diving.
        google_reason = ""
        try:
            body = resp.json()
            google_reason = (
                body.get("error_description")
                or body.get("error")
                or ""
            )
        except Exception:
            google_reason = (resp.text or "")[:200]
        raise DriveAuthError(
            f"Token refresh failed (HTTP {resp.status_code}: {google_reason})"
            if google_reason
            else f"Token refresh failed (HTTP {resp.status_code})"
        )
    return resp.json()


async def get_access_token(user_id: str) -> str:
    """Return a valid access token for ``user_id``, refreshing if expired."""
    token = load_token(user_id)
    if not token:
        raise DriveNotConnected("Google Drive is not connected.")
    if token.get("expiry", 0) > time.time() + 60:
        return token["access_token"]
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        raise DriveAuthError("No refresh token stored — reconnect Google Drive.")
    data = await _refresh_request(refresh_token)
    token["access_token"] = data["access_token"]
    token["expiry"] = time.time() + int(data.get("expires_in", 3600))
    save_token(user_id, token)
    return token["access_token"]
