#!/usr/bin/env python3
"""One-shot migration of the local ``drive_archive`` SQLite corpus to Render Postgres.

Streams the 142k chunks under ``project_id='drive_archive'`` in the local
SQLite store at ``C:\\Users\\shimm\\The_Fork\\data\\the_fork.db`` to the
newly merged admin bulk-insert endpoint on Render. The endpoint is
idempotent (``session.get(PK)`` precheck per row) so this script is safe
to re-run; it additionally maintains a local resume log so already-sent
documents are skipped without a roundtrip.

The local corpus is read read-only via the platform's SQLAlchemy ORM so
``EmbeddingVector`` BLOBs decode into ``np.ndarray`` automatically. Each
chunk's ``project_id`` is overridden to the destination
(``training_material`` or ``projects_folder``) per the manifest produced
by the classifier, then posted as one request per source document
(document row + all chunk rows for that doc).

Run from the repo root with the venv activated::

    python scripts/migrate_drive_archive_to_render.py

The script does NOT modify the local SQLite, the manifest, or any local
tables. It only reads.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# Make ``app.core`` importable when the script is run directly.
sys.path.insert(0, str(Path(r"C:\Users\shimm\The_Fork")))

from app.core.db import SessionLocal  # noqa: E402
from app.core.models import RagChunk  # noqa: E402

# ── Constants ────────────────────────────────────────────────────────────────
MANIFEST = r"C:\tmp\migration_manifest.json"
ENDPOINT = "https://the-fork.onrender.com/v1/admin/corpus/bulk-insert"
TOKEN_FILE = r"C:\tmp\tf_master_key"
LOG_TSV = r"C:\tmp\migrate_log.tsv"
FAILURES = r"C:\tmp\migrate_failures.txt"

SOURCE_PROJECT_ID = "drive_archive"
DEFAULT_FALLBACK_PROJECT_ID = "projects_folder"
PROGRESS_EVERY = 50
REQUEST_TIMEOUT_SECONDS = 120
HTTP_RETRY_ATTEMPTS = 3
HTTP_RETRY_BACKOFF_SECONDS = 5

PROJECT_ROWS = [
    {
        "id": "training_material",
        "name": "Training Material",
        "user_id": "system",
        "status": "active",
    },
    {
        "id": "projects_folder",
        "name": "Projects Folder",
        "user_id": "system",
        "status": "active",
    },
]


# ── Helpers ──────────────────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_token() -> str:
    with open(TOKEN_FILE, "r", encoding="utf-8") as fh:
        token = fh.read().strip()
    if not token:
        raise RuntimeError(f"Empty admin token at {TOKEN_FILE}")
    return token


def _load_manifest() -> list[dict]:
    with open(MANIFEST, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    docs = data.get("documents") or []
    if not isinstance(docs, list):
        raise RuntimeError(f"Manifest 'documents' is not a list in {MANIFEST}")
    return docs


def _load_resume_set() -> set[str]:
    done: set[str] = set()
    if not os.path.exists(LOG_TSV):
        return done
    with open(LOG_TSV, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            doc_id = line.split("\t", 1)[0]
            if doc_id:
                done.add(doc_id)
    return done


def _append_log(doc_id: str, dest_project_id: str, chunks_count: int) -> None:
    os.makedirs(os.path.dirname(LOG_TSV) or ".", exist_ok=True)
    with open(LOG_TSV, "a", encoding="utf-8") as fh:
        fh.write(f"{doc_id}\t{dest_project_id}\t{chunks_count}\n")


def _append_failure(doc_id: str, status_code: int, body_snippet: str) -> None:
    os.makedirs(os.path.dirname(FAILURES) or ".", exist_ok=True)
    with open(FAILURES, "a", encoding="utf-8") as fh:
        fh.write(f"{doc_id}\t{status_code}\t{body_snippet[:500]}\n")


def _post_with_retries(
    session: requests.Session,
    headers: dict,
    payload: dict,
) -> requests.Response:
    """POST with up to ``HTTP_RETRY_ATTEMPTS`` tries on connection errors / timeouts.

    HTTP error responses (e.g. 4xx/5xx) are returned as-is — only network
    errors and timeouts trigger a retry. The caller decides what to do
    with a non-200 status.
    """
    last_exc: Exception | None = None
    for attempt in range(1, HTTP_RETRY_ATTEMPTS + 1):
        try:
            return session.post(
                ENDPOINT,
                headers=headers,
                json=payload,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt < HTTP_RETRY_ATTEMPTS:
                time.sleep(HTTP_RETRY_BACKOFF_SECONDS)
                continue
            raise
    # Unreachable, but keeps type-checkers happy.
    raise RuntimeError(f"HTTP retry loop exited without response: {last_exc}")


def _document_payload_row(doc: dict, dest_project_id: str, uploaded_at: str) -> dict:
    original_path = doc.get("original_path") or doc.get("original_name") or ""
    basename = os.path.basename(original_path) if original_path else (
        doc.get("original_name") or doc["doc_id"]
    )
    return {
        "id": doc["doc_id"],
        "project_id": dest_project_id,
        "original_name": basename,
        "doc_type": "document",
        "doc_role": "other",
        "size": 0,
        "uploaded_at": uploaded_at,
        "file_path": original_path or None,
    }


def _chunk_payload_rows(doc_id: str, dest_project_id: str) -> list[dict]:
    """Read all chunks for ``doc_id`` from local SQLite, override project_id."""
    rows: list[dict] = []
    with SessionLocal() as session:
        records = (
            session.query(RagChunk)
            .filter_by(project_id=SOURCE_PROJECT_ID, doc_id=doc_id)
            .order_by(RagChunk.chunk_index)
            .all()
        )
        for rec in records:
            embedding = rec.embedding
            # ``EmbeddingVector`` returns numpy on read; defensively coerce.
            if hasattr(embedding, "tolist"):
                embedding_list = embedding.tolist()
            else:
                embedding_list = list(embedding)
            rows.append({
                "chunk_id": rec.chunk_id,
                "project_id": dest_project_id,
                "doc_id": rec.doc_id,
                "chunk_index": rec.chunk_index,
                # Postgres TEXT cannot store NUL (\x00) bytes; SQLite tolerates
                # them so some OCR'd PDF chunks carry them through. Strip
                # client-side so the endpoint always sees Postgres-safe text.
                "text": rec.text.replace("\x00", ""),
                "embedding": embedding_list,
                "created_at": rec.created_at,
            })
    return rows


# ── Main ─────────────────────────────────────────────────────────────────────
def main() -> int:
    token = _read_token()
    manifest_docs = _load_manifest()
    resume_done = _load_resume_set()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    http = requests.Session()

    # ── 1. Project priming ────────────────────────────────────────────────
    print(f"[prime] sending {len(PROJECT_ROWS)} project rows...")
    prime_payload = {
        "projects": PROJECT_ROWS,
        "documents": [],
        "chunks": [],
    }
    prime_resp = _post_with_retries(http, headers, prime_payload)
    if prime_resp.status_code != 200:
        print(
            f"[prime][ERROR] HTTP {prime_resp.status_code}: "
            f"{prime_resp.text[:1000]}",
            file=sys.stderr,
        )
        return 2
    print(f"[prime] ok: {prime_resp.json()}")

    # ── 2. Per-document loop ──────────────────────────────────────────────
    total = len(manifest_docs)
    docs_inserted = 0
    docs_skipped = 0
    docs_failed = 0
    chunks_inserted = 0
    chunks_seen = 0

    for idx, doc in enumerate(manifest_docs, start=1):
        doc_id = doc.get("doc_id")
        if not doc_id:
            print(f"[{idx}/{total}][WARN] manifest row missing doc_id; skipping",
                  file=sys.stderr)
            continue

        dest = doc.get("dest_project_id")
        if dest == "unclassified" or not dest:
            print(
                f"[{idx}/{total}][WARN] doc_id={doc_id} is "
                f"'{dest}'; defaulting to {DEFAULT_FALLBACK_PROJECT_ID}",
                file=sys.stderr,
            )
            dest = DEFAULT_FALLBACK_PROJECT_ID
        elif dest not in {"training_material", "projects_folder"}:
            print(
                f"[{idx}/{total}][WARN] doc_id={doc_id} has unknown dest "
                f"'{dest}'; defaulting to {DEFAULT_FALLBACK_PROJECT_ID}",
                file=sys.stderr,
            )
            dest = DEFAULT_FALLBACK_PROJECT_ID

        if doc_id in resume_done:
            docs_skipped += 1
            if idx % PROGRESS_EVERY == 0:
                print(
                    f"[{idx}/{total}] dest={dest} "
                    f"docs_inserted={docs_inserted} "
                    f"chunks_inserted={chunks_inserted} "
                    f"(skipped_via_resume={docs_skipped})"
                )
            continue

        now_iso = _now_iso()
        doc_row = _document_payload_row(doc, dest, now_iso)
        chunk_rows = _chunk_payload_rows(doc_id, dest)

        if not chunk_rows:
            print(
                f"[{idx}/{total}][WARN] doc_id={doc_id} has 0 chunks in "
                f"local SQLite; posting document row only",
                file=sys.stderr,
            )

        payload = {
            "projects": [],
            "documents": [doc_row],
            "chunks": chunk_rows,
        }

        try:
            resp = _post_with_retries(http, headers, payload)
        except Exception as exc:  # noqa: BLE001 — log and continue
            print(
                f"[{idx}/{total}][ERROR] doc_id={doc_id} network failure: {exc}",
                file=sys.stderr,
            )
            _append_failure(doc_id, -1, repr(exc))
            docs_failed += 1
            continue

        if resp.status_code != 200:
            body = resp.text or ""
            print(
                f"[{idx}/{total}][ERROR] doc_id={doc_id} HTTP "
                f"{resp.status_code}: {body[:500]}",
                file=sys.stderr,
            )
            _append_failure(doc_id, resp.status_code, body)
            docs_failed += 1
            continue

        try:
            body_json = resp.json()
        except ValueError:
            body_json = {}
        counts = body_json.get("counts") or {}
        docs_inserted += int(counts.get("documents", 0))
        chunks_inserted += int(counts.get("chunks", 0))
        chunks_seen += int(counts.get("chunks_seen", len(chunk_rows)))

        _append_log(doc_id, dest, len(chunk_rows))

        if idx % PROGRESS_EVERY == 0:
            print(
                f"[{idx}/{total}] dest={dest} "
                f"docs_inserted={docs_inserted} "
                f"chunks_inserted={chunks_inserted}"
            )

    # ── 3. Totals ─────────────────────────────────────────────────────────
    print("─" * 72)
    print("[done] migration loop complete")
    print(f"  manifest docs total : {total}")
    print(f"  docs skipped (resume): {docs_skipped}")
    print(f"  docs failed         : {docs_failed}")
    print(f"  docs inserted (new) : {docs_inserted}")
    print(f"  chunks inserted     : {chunks_inserted}")
    print(f"  chunks seen         : {chunks_seen}")
    print(f"  log                 : {LOG_TSV}")
    print(f"  failures            : {FAILURES}")

    return 0 if docs_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
