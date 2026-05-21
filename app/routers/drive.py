"""Google Drive connection — /v1/drive/* OAuth flow + file import.

All routes require Authorization: Bearer like other /v1/* routes, EXCEPT
/v1/drive/callback — Google calls that directly and cannot send our header,
so it is protected by the single-use OAuth `state` value instead.
"""
import os
import secrets
import time
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse

from app.dependencies import require_api_key
from app.core import drive_auth

router = APIRouter()

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
_DRIVE_API = "https://www.googleapis.com/drive/v3"

# Single-use OAuth state values issued by /connect (in-memory; single process).
_pending_states: set = set()


def _redirect_uri() -> str:
    return os.getenv("GOOGLE_REDIRECT_URI",
                     "http://localhost:8000/v1/drive/callback")


def _configured() -> bool:
    return bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))


async def _exchange_code(code: str) -> Dict[str, Any]:
    """Exchange an auth code for tokens. Overridable seam for tests."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(_TOKEN_URL, data={
            "code": code,
            "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", ""),
            "redirect_uri": _redirect_uri(),
            "grant_type": "authorization_code",
        })
    if resp.status_code != 200:
        raise drive_auth.DriveAuthError(
            f"Code exchange failed (HTTP {resp.status_code})")
    return resp.json()


async def _fetch_email(access_token: str) -> str:
    """Read the connected account's email via the Drive `about` endpoint
    (works with the drive.readonly scope). Overridable seam for tests."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{_DRIVE_API}/about", params={"fields": "user"},
            headers={"Authorization": f"Bearer {access_token}"})
    if resp.status_code != 200:
        return ""
    return resp.json().get("user", {}).get("emailAddress", "")


@router.get("/v1/drive/connect")
async def drive_connect(auth: dict = Depends(require_api_key)):
    # Returns the Google consent URL as JSON — NOT a redirect. A browser cannot
    # attach the Bearer header to a top-level navigation, so the frontend
    # fetches this (header attaches fine on a same-origin fetch), reads
    # auth_url, and does window.location = auth_url itself. Keeps the route
    # gated like every other /v1/* and puts no key in any URL.
    if not _configured():
        raise HTTPException(503, "Google Drive not configured — set "
                                 "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET.")
    state = secrets.token_urlsafe(24)
    _pending_states.add(state)
    from urllib.parse import urlencode
    url = _AUTH_URL + "?" + urlencode({
        "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": _SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })
    return {"auth_url": url}


@router.get("/v1/drive/callback")
async def drive_callback(code: str = Query(""), state: str = Query("")):
    # No Bearer auth here — Google calls this. The single-use state is the gate.
    if state not in _pending_states:
        raise HTTPException(400, "Invalid or expired OAuth state.")
    _pending_states.discard(state)
    if not code:
        raise HTTPException(400, "Missing authorization code.")
    data = await _exchange_code(code)
    access_token = data["access_token"]
    email = await _fetch_email(access_token)
    drive_auth.save_token({
        "access_token": access_token,
        "refresh_token": data.get("refresh_token", ""),
        "expiry": time.time() + int(data.get("expires_in", 3600)),
        "email": email,
    })
    return RedirectResponse("/", status_code=302)
