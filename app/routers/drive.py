"""Google Drive connection — /v1/drive/* OAuth flow + file import.

All routes require Authorization: Bearer like other /v1/* routes, EXCEPT
/v1/drive/callback — Google calls that directly and cannot send our header,
so it is protected by the single-use OAuth `state` value instead.
"""
import base64
import os
import secrets
import time
import uuid
from typing import Any, Dict

import httpx
from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.dependencies import require_api_key, require_user
from app.core import audit, doc_index, drive_auth, file_crypto, projects as store
from app.routers import projects as projects_router
from app.routers.projects import ALLOWED_DOC_EXTENSIONS

router = APIRouter()

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
_DRIVE_API = "https://www.googleapis.com/drive/v3"

# Single-use OAuth state values issued by /connect, mapping state -> issued_at
# epoch. NOTE: process-local — a multi-worker deployment would need a shared
# store (e.g. Redis); this app runs single-worker.
_pending_states: Dict[str, float] = {}
_STATE_TTL = 600  # seconds — pending OAuth states expire after 10 minutes.


def _prune_states() -> None:
    """Drop pending OAuth states older than _STATE_TTL."""
    cutoff = time.time() - _STATE_TTL
    for state in [s for s, issued in _pending_states.items() if issued < cutoff]:
        del _pending_states[state]


def _redirect_uri() -> str:
    return os.getenv("GOOGLE_REDIRECT_URI",
                     "http://localhost:8000/v1/drive/callback")


def _frontend_url() -> str:
    # NOTE: if FRONTEND_URL is a different origin, ensure it is included in the
    # CORS allow_origins list in app/main.py.
    return os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")


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
    _prune_states()
    state = secrets.token_urlsafe(24)
    _pending_states[state] = time.time()
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
async def drive_callback(code: str = Query(""), state: str = Query(""),
                         error: str = Query("")):
    # No Bearer auth here — Google calls this. The single-use state is the gate.
    _prune_states()
    if state not in _pending_states:
        raise HTTPException(400, "Invalid or expired OAuth state.")
    _pending_states.pop(state, None)
    # User clicked "Deny" (or consent otherwise failed): Google sends `error`
    # and no `code`. The state was still consumed above; return gracefully.
    if error:
        return RedirectResponse(f"{_frontend_url()}/?drive=error", status_code=302)
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
    return RedirectResponse(f"{_frontend_url()}/?drive=connected", status_code=302)


@router.get("/v1/drive/status")
async def drive_status(auth: dict = Depends(require_api_key)):
    token = drive_auth.load_token()
    return {
        "connected": token is not None,
        "email": (token or {}).get("email") or None,
        "configured": _configured(),
    }


@router.post("/v1/drive/disconnect")
async def drive_disconnect(auth: dict = Depends(require_api_key)):
    cleared = drive_auth.clear_token()
    return {"status": "ok", "was_connected": cleared}


@router.get("/v1/drive/files")
async def drive_files(q: str = Query(""),
                      auth: dict = Depends(require_api_key)):
    try:
        access_token = await drive_auth.get_access_token()
    except drive_auth.DriveNotConnected:
        raise HTTPException(409, "Google Drive is not connected.")
    except drive_auth.DriveAuthError as e:
        raise HTTPException(409, f"{e} Reconnect Google Drive.")
    from app.blocks.google_drive import GoogleDriveBlock
    result = await GoogleDriveBlock().process(
        q, {"operation": "list", "access_token": access_token, "limit": 50})
    if result.get("status") != "success":
        raise HTTPException(502, result.get("error", "Drive list failed."))
    return {"files": result.get("files", [])}


# ── per-project Drive import — store a Drive file as a project document ──────

class DriveImportRequest(BaseModel):
    file_id: str
    name: str


@router.post("/v1/projects/{project_id}/drive/import", status_code=201)
async def drive_import(project_id: str, req: DriveImportRequest,
                       background_tasks: BackgroundTasks,
                       auth: dict = Depends(require_user)):
    """Import a Google Drive file into a project as a document.

    Downloads the file via the Drive block and stores it through the SAME
    path an uploaded file takes (`projects.py add_document`): encrypted at
    rest, registered as a project document, no analysis run. A Drive-imported
    file is therefore indistinguishable from an uploaded one.
    """
    # Ownership check — scoped to the calling user (same 404-not-403 pattern
    # used in projects.py to avoid leaking project existence to other users).
    proj = store.get_project(project_id, user_id=auth["user_id"])
    if not proj:
        raise HTTPException(404, f"Project '{project_id}' not found")

    # Same 409 handling as the /v1/drive/files route (Task 3).
    try:
        access_token = await drive_auth.get_access_token()
    except drive_auth.DriveNotConnected:
        raise HTTPException(409, "Google Drive is not connected.")
    except drive_auth.DriveAuthError as e:
        raise HTTPException(409, f"{e} Reconnect Google Drive.")

    from app.blocks.google_drive import GoogleDriveBlock
    result = await GoogleDriveBlock().process(
        req.file_id, {"operation": "download", "access_token": access_token})
    if result.get("status") != "success":
        raise HTTPException(502, result.get("error", "Drive download failed."))

    raw_bytes = base64.b64decode(result.get("content_base64", ""))
    # The Drive block's download response has no `filename` key — the caller
    # must supply `name` (the frontend always has it from /v1/drive/files).
    original_name = os.path.basename(str(req.name).replace("\\", "/"))
    # Same extension allowlist a direct upload enforces (projects.py
    # add_document) — applied after the name is known, before writing.
    _, ext = os.path.splitext(original_name.lower())
    if ext not in ALLOWED_DOC_EXTENSIONS:
        raise HTTPException(400, f"File type '{ext}' not allowed")

    # Reuse the upload storage scheme: UUID-prefixed stored filename, written
    # via file_crypto.write_document (encrypted at rest iff DATA_ENCRYPTION_KEY
    # is set) — exactly as projects.py add_document / upload.py do.
    file_id = str(uuid.uuid4())[:8]
    stored_as = f"{file_id}_{original_name}"
    filepath = os.path.join(projects_router.DATA_DIR, stored_as)
    file_crypto.write_document(filepath, raw_bytes)
    size = len(raw_bytes)

    # Register the document through the SAME app.core.projects call.
    doc = store.add_document(
        project_id, original_name, stored_as, filepath, size)
    audit.record("document.added", project_id=project_id,
                 document_id=doc["id"], name=original_name, size=size)
    background_tasks.add_task(doc_index.maybe_eager_index, project_id, doc["id"])
    return {
        "status": "stored",
        "message": (
            f"Added '{original_name}' — classified as {doc['doc_type']} "
            f"(role: {doc['doc_role']}). No analysis was run; ask in chat to "
            f"analyze it."
        ),
        "document": doc,
        "readiness": store.compute_readiness(project_id),
    }
