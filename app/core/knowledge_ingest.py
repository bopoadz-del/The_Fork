"""Index admin-added reference works OUTSIDE the live web process.

``POST /v1/admin/knowledge/documents`` only stores a reference work: it
records the row as pending (``metadata.indexing.status == "pending"``) and
returns. Extraction of a whole book -- hundreds of pages, isolated page
batches, the live embedder -- is the ingest task's job, where memory is sized
for it and a slow document does not hold a web worker.

:func:`process_pending_knowledge_documents` is that phase. It runs at the
start of every ingest-task run, before (and independent of) the Drive phase.
It touches ONLY pending admin-knowledge rows in the general-knowledge
project(s): each is indexed once and stamped with its final indexing status,
so a second run finds nothing pending and does nothing -- no extraction, no
embedding, no writes.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

#: ``metadata.indexing.status`` of a stored, not-yet-indexed reference work.
PENDING = "pending"

#: Provenance the admin knowledge route stamps on its rows.
ADMIN_KNOWLEDGE_PROVENANCE = "admin_knowledge"


def pending_indexing_metadata() -> Dict[str, Any]:
    """The metadata that marks a stored reference work as waiting for ingest."""
    return {"indexing": {
        "status": PENDING,
        "detail": "stored; indexed by the next ingest-task run",
    }}


def general_knowledge_projects() -> List[str]:
    """Every configured general-knowledge project id."""
    from app.core.system_projects import general_knowledge_env

    return [p.strip() for p in general_knowledge_env().split(",") if p.strip()]


def is_pending(doc: Dict[str, Any]) -> bool:
    """True for an admin-knowledge row stored but not yet indexed."""
    from app.core.projects import coerce_document_metadata

    meta = coerce_document_metadata(doc.get("metadata"))
    if meta.get("provenance") != ADMIN_KNOWLEDGE_PROVENANCE:
        return False
    return (meta.get("indexing") or {}).get("status") == PENDING


def pending_knowledge_documents() -> List[Dict[str, Any]]:
    """Pending admin-knowledge rows across the general-knowledge projects."""
    from app.core import projects as store

    out: List[Dict[str, Any]] = []
    for project_id in general_knowledge_projects():
        if not store.get_project(project_id):
            continue
        out.extend(d for d in store.list_documents(project_id) if is_pending(d))
    return out


def process_pending_knowledge_documents(
    *,
    dry_run: bool = False,
    log: Optional[Callable[[str], None]] = None,
) -> Dict[str, int]:
    """Index every pending admin-knowledge document once.

    Returns ``{"pending", "indexed", "failed"}``. Each processed row gets its
    final ``metadata.indexing`` (ok or error, chunk count, pages without a
    text layer), so it is never picked up again: nothing pending means
    nothing is read, embedded or written. ``dry_run`` only counts.
    """
    from app.core import doc_index, projects as store

    say = log or logger.info
    pending = pending_knowledge_documents()
    tally = {"pending": len(pending), "indexed": 0, "failed": 0}
    if dry_run or not pending:
        return tally
    for doc in pending:
        project_id = doc.get("project_id") or ""
        doc_id = doc["id"]
        say(f"knowledge: indexing {doc_id} ({doc.get('original_name')}) in {project_id}")
        try:
            result = doc_index.index_document(project_id, doc_id)
            record = doc_index.indexing_record(result)
        except Exception as exc:  # noqa: BLE001 — one book must not stop the phase
            logger.exception("knowledge: indexing %s failed", doc_id)
            result = {}
            record = {"status": "error", "error": type(exc).__name__,
                      "chunks": 0, "detail": str(exc)[:300]}
        # Keep what index_document already recorded on the row (pages
        # without a text layer), then stamp the final status over it.
        current = store.get_document(doc_id) or {}
        indexing = dict(
            (store.coerce_document_metadata(current.get("metadata")).get("indexing")) or {}
        )
        indexing.update(record)
        store.update_document_metadata(doc_id, {"indexing": indexing})
        if record.get("status") == "ok" and int(result.get("indexed") or 0) > 0:
            tally["indexed"] += 1
        else:
            tally["failed"] += 1
        say(
            f"knowledge: {doc_id} status={record.get('status')} "
            f"chunks={record.get('chunks')} "
            f"pages_without_text={indexing.get('pages_without_text', 0)}"
        )
    return tally
