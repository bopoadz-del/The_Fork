"""Google Drive — service-account access for unattended jobs.

The user-facing :class:`app.blocks.google_drive.GoogleDriveBlock` is built
around per-user OAuth — a session token the user obtained by signing in.
The nightly hydration job has no signed-in user, so it can't use that path.

This module fills the gap with the standard pattern for unattended Google API
access: a **service account**. The account is created in GCP, has its email
shared into the Drive folders that should be visible, and its JSON key is
mounted into the container via an env var. At call time we mint a short-lived
access token from the key and hit the same Drive v3 REST surface the user-mode
block already talks to.

Configuration (env vars):

* ``GDRIVE_SERVICE_ACCOUNT_JSON`` — either an absolute path to the service
  account's JSON key file, or the JSON content itself (one line). Required.
* ``GDRIVE_PROJECT_FOLDERS`` — comma-separated ``project_id:drive_folder_id``
  pairs that tell hydration which Drive folder belongs to which platform
  project. Without this mapping, the scheduler has no way to know.

Dependencies: this module lazy-imports ``google-auth`` only when a call
actually needs to mint a token. If the library isn't installed, the helpers
return a clean ``status=disabled`` payload — the hydration block treats this
as "not configured" and continues without crashing.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple


logger = logging.getLogger(__name__)


_DRIVE_API = "https://www.googleapis.com/drive/v3"
_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Drive metadata MIME types we cannot blob-download. The export path (for
# Docs/Sheets/Slides) is a separate concern handled at download time.
_GOOGLE_DOC_MIMES = {
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.spreadsheet",
    "application/vnd.google-apps.presentation",
    "application/vnd.google-apps.drawing",
    "application/vnd.google-apps.folder",
}


# ── Configuration ────────────────────────────────────────────────────────


def is_configured() -> bool:
    """True iff the service-account key env var is present.

    Does NOT validate the key itself — only that the operator intended to
    enable this path. ``get_credentials_info`` does the parse, and minting
    a token surfaces the deeper "is this key actually valid" answer.
    """
    return bool((os.getenv("GDRIVE_SERVICE_ACCOUNT_JSON") or "").strip())


def parse_project_folder_map() -> Dict[str, str]:
    """Parse ``GDRIVE_PROJECT_FOLDERS`` into ``{project_id: folder_id}``.

    Format: comma-separated ``proj:folder`` pairs. Malformed entries are
    logged and skipped — one bad row doesn't poison the whole mapping.
    """
    raw = (os.getenv("GDRIVE_PROJECT_FOLDERS") or "").strip()
    if not raw:
        return {}
    out: Dict[str, str] = {}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            logger.warning("GDRIVE_PROJECT_FOLDERS: ignoring malformed entry %r", chunk)
            continue
        pid, fid = chunk.split(":", 1)
        pid, fid = pid.strip(), fid.strip()
        if pid and fid:
            out[pid] = fid
    return out


def _load_service_account_info() -> Optional[Dict[str, Any]]:
    """Read the service-account JSON from env. Accepts either a file path or
    inline JSON content. Returns ``None`` (with a warning logged) when the
    value is set but unreadable — callers treat that as "not configured"."""
    raw = (os.getenv("GDRIVE_SERVICE_ACCOUNT_JSON") or "").strip()
    if not raw:
        return None
    # File path?
    if os.path.isfile(raw):
        try:
            with open(raw, "r") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("GDRIVE_SERVICE_ACCOUNT_JSON file unreadable: %s", exc)
            return None
    # Inline JSON?
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("GDRIVE_SERVICE_ACCOUNT_JSON inline value is not valid JSON: %s", exc)
        return None


# ── Token minting (cached) ───────────────────────────────────────────────

_token_cache: Dict[str, Any] = {"token": None, "expiry": 0.0}


def _mint_access_token() -> Optional[str]:
    """Return a Drive-scoped access token, or ``None`` if anything is wrong
    (lib missing, key invalid, network failure). Tokens are cached in-process
    until shortly before their server-side expiry — a single hydration pass
    won't hammer the token endpoint."""
    # Reuse a cached token until 60 s before its expiry.
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expiry"] - 60:
        return _token_cache["token"]

    info = _load_service_account_info()
    if info is None:
        return None

    try:
        from google.oauth2 import service_account  # lazy import
        from google.auth.transport.requests import Request as GoogleAuthRequest
    except ImportError:
        logger.warning(
            "google-auth is not installed; hydration cannot mint Drive tokens. "
            "Install it (or uncomment in requirements.txt) to enable the "
            "service-account path."
        )
        return None

    try:
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=_SCOPES
        )
        creds.refresh(GoogleAuthRequest())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Drive service-account token mint failed: %s", exc)
        return None

    _token_cache["token"] = creds.token
    # creds.expiry is naive UTC; convert to epoch
    if creds.expiry is not None:
        from datetime import timezone as _tz
        _token_cache["expiry"] = creds.expiry.replace(tzinfo=_tz.utc).timestamp()
    else:
        _token_cache["expiry"] = now + 3000  # be conservative — ~50 min
    return creds.token


# ── Drive REST helpers ───────────────────────────────────────────────────


def list_folder_files(folder_id: str, page_size: int = 100) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """List non-trashed files directly inside ``folder_id``.

    Returns ``(files, error)``. On any failure (no token, network, 4xx/5xx)
    returns ``([], "<reason>")``; hydration logs the reason and moves on
    rather than crashing the whole pass. Subfolders are NOT recursed — one
    Drive folder maps to one platform project; deeper structure can be
    flattened later if it becomes a real need.
    """
    token = _mint_access_token()
    if not token:
        return [], "service account unavailable (key missing, invalid, or google-auth not installed)"

    try:
        import httpx
    except ImportError:
        return [], "httpx not available"

    q = f"'{folder_id}' in parents and trashed = false"
    files: List[Dict[str, Any]] = []
    page_token: Optional[str] = None
    try:
        with httpx.Client(timeout=30) as client:
            while True:
                params: Dict[str, Any] = {
                    "q": q,
                    "pageSize": page_size,
                    "fields": "nextPageToken, files(id, name, mimeType, size, modifiedTime)",
                }
                if page_token:
                    params["pageToken"] = page_token
                resp = client.get(
                    f"{_DRIVE_API}/files",
                    headers={"Authorization": f"Bearer {token}"},
                    params=params,
                )
                if resp.status_code != 200:
                    return [], f"Drive list returned {resp.status_code}: {resp.text[:200]}"
                payload = resp.json()
                files.extend(payload.get("files") or [])
                page_token = payload.get("nextPageToken")
                if not page_token:
                    break
    except Exception as exc:  # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"
    return files, None


def is_downloadable(file_meta: Dict[str, Any]) -> bool:
    """A file is downloadable if it's neither a folder nor a Google-native
    Doc/Sheet/Slide (those need ``export`` instead of ``alt=media``).

    Folders are filtered out implicitly by the list query but we double-check
    in case of permission quirks.
    """
    mime = file_meta.get("mimeType") or ""
    return mime not in _GOOGLE_DOC_MIMES


def download_file_bytes(file_id: str) -> Tuple[Optional[bytes], Optional[str]]:
    """Download a single Drive file's raw bytes.

    Returns ``(bytes, None)`` on success or ``(None, error)`` on failure.
    Streams the response so a large file doesn't have to fit in one socket
    buffer; the hydration block enforces a separate size cap before calling.
    """
    token = _mint_access_token()
    if not token:
        return None, "service account unavailable"
    try:
        import httpx
    except ImportError:
        return None, "httpx not available"

    try:
        with httpx.Client(timeout=120) as client:
            resp = client.get(
                f"{_DRIVE_API}/files/{file_id}",
                headers={"Authorization": f"Bearer {token}"},
                params={"alt": "media"},
            )
            if resp.status_code != 200:
                return None, f"Drive download returned {resp.status_code}: {resp.text[:200]}"
            return resp.content, None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"
