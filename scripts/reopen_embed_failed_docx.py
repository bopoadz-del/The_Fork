#!/usr/bin/env python3
"""Re-open TEXT_SPARSE .docx that were stamped current after embed failure.

Not a Drive crawl, not p1b, and not a status-to-green hand stamp.
Same document id. Only ``extractor_version`` is rewritten.

Usage (Render worker the-fork-ingest, from /app):
    python scripts/reopen_embed_failed_docx.py [--dry-run] [--limit N]

Selects retrieval_visible ``.docx`` where
    extractor_version == EXTRACTOR_VERSION
    ingest_status == TEXT_SPARSE
    and the index ledger entry has ``rag_error`` or ``rag_indexed == 0``.

That discriminator is what separates embed-failure (re-open) from genuinely
thin text (leave closed). INDEXED rows are never selected.

For each match, set extractor_version to the sentinel
``{EXTRACTOR_VERSION}/embed-failed``. The column is never nulled.
``docx_stale_extractor_open`` then re-qualifies the row because the sentinel
is not EXTRACTOR_VERSION.

``--dry-run`` prints ``doc_id=… rag_error=…`` for each match and ``count=N``.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("RAG_EMBEDDING_MODEL", "fake")


def _sentinel() -> str:
    from app.core.ingest_status import EXTRACTOR_VERSION

    return f"{EXTRACTOR_VERSION}/embed-failed"


def ledger_embed_failed(entry: Optional[Mapping[str, Any]]) -> bool:
    """True when the index ledger says embed did not land."""
    if not entry:
        return False
    if entry.get("rag_error"):
        return True
    return entry.get("rag_indexed") == 0


def _ledger_by_doc_id() -> Dict[str, Dict[str, Any]]:
    from sqlalchemy import select

    from app.core.doc_index import _index_from_row, init_db as init_doc_index
    from app.core.db import SessionLocal
    from app.core.models import DocIndex

    init_doc_index()
    out: Dict[str, Dict[str, Any]] = {}
    with SessionLocal() as session:
        rows = session.scalars(select(DocIndex)).all()
        for row in rows:
            data = _index_from_row(row) or {}
            for entry in data.get("documents") or []:
                if not isinstance(entry, dict):
                    continue
                did = entry.get("document_id")
                if did:
                    out[str(did)] = entry
    return out


def select_embed_failed_docx_rows(*, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """retrieval_visible .docx stamped current + TEXT_SPARSE + embed failed."""
    from sqlalchemy import select

    from app.core import ingest_status as ist
    from app.core import projects as projects_mod
    from app.core.db import SessionLocal
    from app.core.models import Document
    from app.core.projects import _document_as_dict

    projects_mod.init_db()
    ledger = _ledger_by_doc_id()
    selected: List[Dict[str, Any]] = []
    with SessionLocal() as session:
        rows = session.scalars(
            select(Document).where(Document.retrieval_visible.is_(True))
        ).all()
        docs = [_document_as_dict(document) for document in rows]
    for doc in docs:
        if ist.document_extension(doc) != ".docx":
            continue
        if (doc.get("extractor_version") or "") != ist.EXTRACTOR_VERSION:
            continue
        if doc.get("ingest_status") != ist.TEXT_SPARSE:
            continue
        entry = ledger.get(doc["id"])
        if not ledger_embed_failed(entry):
            continue
        selected.append({**doc, "_rag_error": (entry or {}).get("rag_error")})
    if limit is not None:
        selected = selected[: max(0, int(limit))]
    return selected


def stamp_embed_failed_sentinel(doc_id: str) -> None:
    """Rewrite extractor_version only. Never null. Never touch ingest_status."""
    from app.core.db import SessionLocal
    from app.core.models import Document

    sentinel = _sentinel()
    with SessionLocal() as session:
        row = session.get(Document, doc_id)
        if row is None:
            return
        row.extractor_version = sentinel
        session.commit()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print doc_id and rag_error. No writes.",
    )
    ap.add_argument("--limit", type=int, default=None, help="Cap selected rows")
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    rows = select_embed_failed_docx_rows(limit=args.limit)
    if args.dry_run:
        for row in rows:
            print(
                f"doc_id={row['id']} rag_error={row.get('_rag_error') or '-'}"
            )
        print(f"count={len(rows)}")
        return 0

    for row in rows:
        stamp_embed_failed_sentinel(row["id"])
        print(
            f"REOPENED doc_id={row['id']} "
            f"extractor_version={_sentinel()} "
            f"rag_error={row.get('_rag_error') or '-'}"
        )
    print(f"count={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
