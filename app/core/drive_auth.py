"""App-wide Google Drive OAuth token store + refresh.

One connection for the whole app. The token — including the refresh token, a
secret — is persisted to DATA_DIR/google_drive_token.json via file_crypto, so
it is encrypted at rest when DATA_ENCRYPTION_KEY is set.
"""
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from app.core import file_crypto

_TOKEN_URL = "https://oauth2.googleapis.com/token"


class DriveNotConnected(Exception):
    """No Google Drive token is stored."""


class DriveAuthError(Exception):
    """Token exchange or refresh failed."""


def _token_path() -> Path:
    return Path(os.getenv("DATA_DIR", "./data")) / "google_drive_token.json"


def save_token(token: Dict[str, Any]) -> None:
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    file_crypto.write_document(str(path), json.dumps(token).encode("utf-8"))


def load_token() -> Optional[Dict[str, Any]]:
    path = _token_path()
    if not path.exists():
        return None
    return json.loads(file_crypto.read_document(str(path)).decode("utf-8"))


def clear_token() -> bool:
    path = _token_path()
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
        raise DriveAuthError(f"Token refresh failed (HTTP {resp.status_code})")
    return resp.json()


async def get_access_token() -> str:
    """Return a valid access token, refreshing if it has expired."""
    token = load_token()
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
    save_token(token)
    return token["access_token"]
