"""Project entity — groups documents and gates project-level analytics.

Roadmap V2 · Part 0.1 (Project entity) + 0.2 (readiness gate).

A Project is the backbone the platform was missing: documents are no longer
processed in isolation, and project-level analytics (progress tracking, earned
value) stay inert until the project is genuinely set up.

SQLAlchemy-backed via app.core.db — unified The Fork schema.
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select, text as sqla_text

from app.core.db import SessionLocal, engine, get_database_url
from app.core.models import Document, Project, ProjectFact

# ── pilot master-corpus alias ───────────────────────────────────────────────
# Per-project Drive approval/indexing is not pilot-ready. Expose the existing
# full-drive corpus (currently stored under project_id "projects_folder") as a
# single admin-visible pilot project without duplicating chunks or re-importing
# Drive. This is a temporary alias; proper per-project trees come post-pilot.
MASTER_CORPUS_PROJECT_ID = os.getenv("MASTER_CORPUS_PROJECT_ID", "dar_al_arkan_master")
MASTER_CORPUS_SOURCE_PROJECT_ID = os.getenv(
    "MASTER_CORPUS_SOURCE_PROJECT_ID", "projects_folder"
)
MASTER_CORPUS_NAME = os.getenv("MASTER_CORPUS_NAME", "Dar Al Arkan Master Corpus")


def _master_corpus_source(project_id: Optional[str]) -> Optional[str]:
    """Return the backing project_id for a master-corpus alias, if any."""
    if project_id == MASTER_CORPUS_PROJECT_ID:
        return MASTER_CORPUS_SOURCE_PROJECT_ID
    return None


# ── document roles that feed the readiness gate ─────────────────────────────
ROLE_BASELINE = "baseline_schedule"
ROLE_DAILY = "daily_report"
ROLE_WEEKLY = "weekly_report"
ROLE_OTHER = "other"
VALID_ROLES = {ROLE_BASELINE, ROLE_DAILY, ROLE_WEEKLY, ROLE_OTHER}

_lock = threading.Lock()
_initialized = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_sqlite_parent_dir() -> None:
    url = get_database_url()
    if url.startswith("sqlite:///"):
        parent = os.path.dirname(url[len("sqlite:///") :])
        if parent:
            os.makedirs(parent, exist_ok=True)


def _project_as_dict(project: Project) -> Dict[str, Any]:
    return {
        "id": project.id,
        "name": project.name,
        "client": project.client,
        "status": project.status,
        "aconex_connected": bool(project.aconex_connected),
        "user_id": project.user_id,
        "created_at": project.created_at,
        "is_approved": bool(getattr(project, "is_approved", True)),
        "origin": getattr(project, "origin", "user_create") or "user_create",
        "is_master_corpus": False,
    }


def _document_as_dict(document: Document) -> Dict[str, Any]:
    return {
        "id": document.id,
        "project_id": document.project_id,
        "original_name": document.original_name,
        "stored_as": document.stored_as,
        "file_path": document.file_path,
        "doc_type": document.doc_type,
        "doc_role": document.doc_role,
        "size": document.size,
        "uploaded_at": document.uploaded_at,
        "content_sha256": document.content_sha256,
    }


def _fact_as_dict(fact: ProjectFact) -> Dict[str, Any]:
    return {
        "id": fact.id,
        "project_id": fact.project_id,
        "key": fact.key,
        "value": fact.value,
        "source_document": fact.source_document,
        "confidence": fact.confidence,
        "updated_at": fact.updated_at,
    }


def init_db() -> None:
    """Create the schema if absent. Idempotent — safe to call on every startup.

    Also runs lightweight in-place column patches for legacy SQLite
    databases that were created before recent migrations landed. Prod
    Postgres applies the same changes via Alembic; this branch keeps
    local dev / fresh test environments self-healing without requiring
    an explicit `alembic upgrade head` run.
    """
    global _initialized
    with _lock:
        from app.core.users import init_db as init_users_db

        init_users_db()
        _ensure_sqlite_parent_dir()
        Project.__table__.create(bind=engine, checkfirst=True)
        Document.__table__.create(bind=engine, checkfirst=True)
        ProjectFact.__table__.create(bind=engine, checkfirst=True)
        _patch_legacy_columns()
        _initialized = True


def _patch_legacy_columns() -> None:
    """Add columns to legacy tables when they're missing.

    SQLite only — Postgres deployments are managed by Alembic. This
    function exists because checkfirst=True on Table.create() does NOT
    add new columns to existing tables; we have to ALTER manually for
    dev environments + tests.

    Currently handles:
      * projects.is_approved (Alembic 0004)
      * projects.origin       (Alembic 0005)
    """
    url = get_database_url()
    if not url.startswith("sqlite"):
        return  # Postgres is migration-managed.
    try:
        with engine.connect() as conn:
            cols = {row[1] for row in conn.execute(
                sqla_text("PRAGMA table_info(projects)")
            )}
            if "is_approved" not in cols:
                conn.execute(sqla_text(
                    "ALTER TABLE projects "
                    "ADD COLUMN is_approved BOOLEAN NOT NULL DEFAULT 1"
                ))
                conn.commit()
            if "origin" not in cols:
                conn.execute(sqla_text(
                    "ALTER TABLE projects "
                    "ADD COLUMN origin TEXT NOT NULL DEFAULT 'user_create'"
                ))
                conn.commit()
    except Exception:
        # Don't crash boot on a dev-environment patch failure — the
        # next call to a feature that needs the column will surface
        # the real error with a clearer stack.
        import logging
        logging.getLogger(__name__).warning(
            "projects.is_approved column patch skipped", exc_info=True,
        )


def _ensure_db() -> None:
    if not _initialized:
        init_db()


# ── classification ──────────────────────────────────────────────────────────

def classify_doc_type(filename: str) -> str:
    """Coarse document-type guess from the filename (display only)."""
    n = (filename or "").lower()
    _, ext = os.path.splitext(n)
    if ext in {".xer", ".mpp"} or "primavera" in n or "p6" in n:
        return "schedule"
    if ext == ".ifc":
        return "bim"
    if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        return "photo"
    if "boq" in n or "bill of quant" in n:
        return "boq"
    if "contract" in n or "agreement" in n:
        return "contract"
    if "spec" in n:
        return "specification"
    if "drawing" in n or ext == ".dwg":
        return "drawing"
    if "schedule" in n or "programme" in n or "program" in n:
        return "schedule"
    return "document"


def classify_doc_role(filename: str) -> str:
    """Map a filename to a readiness role. Conservative — defaults to 'other'."""
    n = (filename or "").lower()
    if "baseline" in n:
        return ROLE_BASELINE
    if "daily" in n:
        return ROLE_DAILY
    if "weekly" in n:
        return ROLE_WEEKLY
    return ROLE_OTHER


# ── projects ────────────────────────────────────────────────────────────────

def create_project(
    name: str,
    client: Optional[str] = None,
    user_id: str = "system",
    *,
    is_approved: bool = True,
    project_id: Optional[str] = None,
    origin: str = "user_create",
) -> Dict[str, Any]:
    """Create a project row.

    PR A: ``is_approved`` defaults to True for both user-created and
    admin-created flows. The approve-from-Drive endpoint also passes
    True explicitly. A future "detected but pending" code path can
    pass False to create a candidate row that's hidden from the user
    rail until an admin flips it.

    PR B: ``origin`` records how the row was created. The admin page
    filters on origin='admin_drive_approved' so user-created rows
    don't appear in the admin's approved list. Allowed values:
    'user_create' (default), 'admin_drive_approved', 'user_drive_import'.

    ``project_id`` lets a caller pre-supply the id (used by the
    approve-from-Drive flow so the slug is human-friendly instead of
    a random hex).
    """
    _ensure_db()
    pid = project_id or str(uuid.uuid4())[:8]
    with _lock:
        with SessionLocal() as session:
            session.add(
                Project(
                    id=pid,
                    name=name,
                    client=client,
                    status="active",
                    aconex_connected=False,
                    user_id=user_id,
                    is_approved=is_approved,
                    origin=origin,
                    created_at=_now(),
                )
            )
            session.commit()
    return get_project(pid)


def list_projects(
    user_id: Optional[str] = None,
    *,
    include_admin_approved: bool = False,
) -> List[Dict[str, Any]]:
    """List projects.

    PR D — visibility model:
      * When ``user_id`` is None: every row (admin / internal use only).
      * When ``user_id`` is set + ``include_admin_approved=False``: rows
        owned by the caller only (legacy behaviour).
      * When ``user_id`` is set + ``include_admin_approved=True``: rows
        owned by the caller PLUS rows where origin='admin_drive_approved'
        AND is_approved=True (the platform-wide canonical projects).
        ``is_approved=False`` rows are hidden from non-owners regardless
        of origin — defensive against future "detected but not yet
        approved" rows that could otherwise leak.

    Pilot: if the master-corpus source project is visible to the caller,
    the virtual ``dar_al_arkan_master`` alias is appended to the list.
    """
    from sqlalchemy import or_, and_

    _ensure_db()
    with SessionLocal() as session:
        # Soft-archived projects are hidden from every listing (the "Delete"
        # action archives rather than deletes — see archive_project).
        stmt = (
            select(Project)
            .where(Project.status != "archived")
            .order_by(Project.created_at.desc())
        )
        if user_id is not None:
            if include_admin_approved:
                stmt = stmt.where(
                    or_(
                        Project.user_id == user_id,
                        and_(
                            Project.origin == "admin_drive_approved",
                            Project.is_approved.is_(True),
                        ),
                    )
                )
            else:
                stmt = stmt.where(Project.user_id == user_id)
        rows = session.scalars(stmt).all()
    out = []
    for project in rows:
        p = _project_as_dict(project)
        p["readiness"] = compute_readiness(p["id"])
        p["document_count"] = len(list_documents(p["id"]))
        out.append(p)

    # Expose the pilot master-corpus alias when the backing corpus is visible.
    master = get_project(
        MASTER_CORPUS_PROJECT_ID,
        user_id=user_id,
        include_admin_approved=include_admin_approved,
    )
    if master is not None:
        master["is_master_corpus"] = True
        master["document_count"] = len(list_documents(MASTER_CORPUS_SOURCE_PROJECT_ID))
        # Pilot: the master corpus is the canonical starting point, so it
        # always appears first regardless of creation date.
        out.insert(0, master)

    return out


def get_project(
    project_id: str,
    user_id: Optional[str] = None,
    *,
    include_admin_approved: bool = False,
) -> Optional[Dict[str, Any]]:
    """Load a project the caller can access.

    PR D — non-owners may also read admin-approved platform projects
    when ``include_admin_approved=True``. ``is_approved=False`` rows
    stay owner-only regardless of origin (defensive — admins shouldn't
    leak detected-but-pending candidates to users).

    Pilot: a virtual master-corpus project (default ``dar_al_arkan_master``)
    is backed by the existing full-drive corpus (default ``projects_folder``).
    It appears as a first-class project without duplicating chunks.
    """
    _ensure_db()
    source_id = _master_corpus_source(project_id) or project_id
    with SessionLocal() as session:
        project = session.get(Project, source_id)
    if not project:
        return None
    # Soft-archived projects are treated as gone everywhere they're read
    # (UI detail, ownership gates, retrieval scoping) — but the row + its RAG
    # chunks stay in the DB. See archive_project.
    if getattr(project, "status", "active") == "archived":
        return None
    is_alias = source_id != project_id
    if user_id is not None and project.user_id != user_id:
        if is_alias:
            # The master-corpus alias is treated as an admin-approved platform
            # project: visible to any authenticated user when the caller asks
            # for platform projects, otherwise owner-only.
            allowed = include_admin_approved and bool(
                getattr(project, "is_approved", True)
            )
        else:
            allowed = (
                include_admin_approved
                and getattr(project, "origin", "user_create") == "admin_drive_approved"
                and bool(getattr(project, "is_approved", True))
            )
        if not allowed:
            return None
    proj = _project_as_dict(project)
    # Virtual master-corpus project: expose alias id/name but keep the source
    # corpus behind it.
    if source_id != project_id:
        proj["id"] = project_id
        proj["name"] = MASTER_CORPUS_NAME
        proj["origin"] = "admin_drive_approved"
        proj["is_approved"] = True
        proj["is_master_corpus"] = True
    proj["documents"] = list_documents(source_id)
    proj["document_count"] = len(proj["documents"])
    proj["readiness"] = compute_readiness(source_id)
    return proj


def project_owner(project_id: str) -> Optional[str]:
    """Return the user_id that owns the project, or None if the project doesn't exist."""
    _ensure_db()
    with SessionLocal() as session:
        project = session.get(Project, project_id)
    return project.user_id if project else None


def archive_project(project_id: str) -> bool:
    """Soft-delete: hide the project from listings, detail, ownership gates and
    retrieval WITHOUT removing the row. `chunks.project_id` is ON DELETE
    CASCADE, so keeping the row is what preserves the RAG — the operator
    principle 'delete the UI, never the RAG; build on it only'. Reversible:
    set status back to 'active' to restore. Returns False if not found."""
    _ensure_db()
    with _lock:
        with SessionLocal() as session:
            project = session.get(Project, project_id)
            if not project:
                return False
            project.status = "archived"
            session.commit()
            return True


def delete_project(project_id: str) -> bool:
    """HARD delete — removes the row and (via ON DELETE CASCADE) its documents
    AND RAG chunks. Reserved for genuine admin cleanup; the user-facing Delete
    action uses archive_project so the RAG is never destroyed."""
    _ensure_db()
    with _lock:
        with SessionLocal() as session:
            project = session.get(Project, project_id)
            if not project:
                return False
            session.delete(project)
            session.commit()
            return True


def set_aconex(project_id: str, connected: bool) -> bool:
    """Set the Aconex connection flag. Stub for the full connector (Roadmap V2)."""
    _ensure_db()
    with _lock:
        with SessionLocal() as session:
            project = session.get(Project, project_id)
            if not project:
                return False
            project.aconex_connected = connected
            session.commit()
            return True


# ── documents ───────────────────────────────────────────────────────────────

def add_document(
    project_id: str,
    original_name: str,
    stored_as: Optional[str] = None,
    file_path: Optional[str] = None,
    size: int = 0,
    role: Optional[str] = None,
    content_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    """Register a document under a project. Storing only — runs no analysis."""
    _ensure_db()
    did = str(uuid.uuid4())[:8]
    doc_type = classify_doc_type(original_name)
    doc_role = role if role in VALID_ROLES else classify_doc_role(original_name)
    with _lock:
        with SessionLocal() as session:
            session.add(
                Document(
                    id=did,
                    project_id=project_id,
                    original_name=original_name,
                    stored_as=stored_as,
                    file_path=file_path,
                    doc_type=doc_type,
                    doc_role=doc_role,
                    size=size,
                    uploaded_at=_now(),
                    content_sha256=content_sha256,
                )
            )
            session.commit()
    with SessionLocal() as session:
        document = session.get(Document, did)
    assert document is not None
    return _document_as_dict(document)


def find_document_by_sha(
    project_id: str, content_sha256: str,
) -> Optional[Dict[str, Any]]:
    """Return the FIRST existing document in this project with this content
    hash, or None. Used by the Drive walker to skip unchanged files on
    re-walk. Returns None for empty/None hashes so a missing sha cannot
    accidentally match other null-sha rows."""
    if not content_sha256:
        return None
    _ensure_db()
    with SessionLocal() as session:
        document = session.scalars(
            select(Document)
            .where(
                Document.project_id == project_id,
                Document.content_sha256 == content_sha256,
            )
            .order_by(Document.uploaded_at)
            .limit(1)
        ).first()
    return _document_as_dict(document) if document else None


def list_documents(project_id: str) -> List[Dict[str, Any]]:
    _ensure_db()
    source_id = _master_corpus_source(project_id) or project_id
    with SessionLocal() as session:
        rows = session.scalars(
            select(Document)
            .where(Document.project_id == source_id)
            .order_by(Document.uploaded_at)
        ).all()
    return [_document_as_dict(document) for document in rows]


def get_document(doc_id: str) -> Optional[Dict[str, Any]]:
    _ensure_db()
    with SessionLocal() as session:
        document = session.get(Document, doc_id)
    return _document_as_dict(document) if document else None


def delete_document(doc_id: str) -> Optional[Dict[str, Any]]:
    """Delete a document row. Returns the deleted row (so the caller can
    purge the file from disk), or None if it did not exist.

    Also deletes the document's chunks from the RagChunk table. Postgres
    has an ON DELETE CASCADE FK so the cascade is implicit there; SQLite
    (used by dev / tests) doesn't enforce FKs by default, so we delete
    explicitly to keep search results consistent across backends. Without
    this, a search after deletion can still surface chunks from the
    removed document because the hybrid retriever queries the chunks
    table directly.
    """
    doc = get_document(doc_id)
    if not doc:
        return None
    project_id = doc.get("project_id")
    with _lock:
        with SessionLocal() as session:
            document = session.get(Document, doc_id)
            if document:
                session.delete(document)
                if project_id:
                    from app.core.models import RagChunk  # local: avoid circular
                    session.execute(
                        delete(RagChunk).where(
                            RagChunk.project_id == project_id,
                            RagChunk.doc_id == doc_id,
                        )
                    )
                session.commit()
    return doc


def purge_documents_older_than(days: int) -> List[Dict[str, Any]]:
    """Delete document rows older than `days`. Returns the purged rows
    (Roadmap V2 · Epic 6 — data retention)."""
    _ensure_db()
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with SessionLocal() as session:
        rows = session.scalars(
            select(Document).where(Document.uploaded_at < cutoff)
        ).all()
        purged = [_document_as_dict(document) for document in rows]
    with _lock:
        with SessionLocal() as session:
            session.execute(delete(Document).where(Document.uploaded_at < cutoff))
            session.commit()
    return purged


# ── readiness gate (Roadmap V2 · 0.2) ───────────────────────────────────────

def compute_readiness(project_id: str) -> Dict[str, Any]:
    """A project is 'ready' for progress tracking only once it has a baseline
    schedule, at least one daily and one weekly report, and Aconex connected."""
    source_id = _master_corpus_source(project_id) or project_id
    docs = list_documents(source_id)
    roles = [d["doc_role"] for d in docs]
    with SessionLocal() as session:
        project = session.get(Project, source_id)
    aconex = bool(project.aconex_connected) if project else False

    baseline = ROLE_BASELINE in roles
    daily = roles.count(ROLE_DAILY)
    weekly = roles.count(ROLE_WEEKLY)

    missing: List[str] = []
    if not baseline:
        missing.append("baseline_schedule")
    if daily < 1:
        missing.append("daily_reports")
    if weekly < 1:
        missing.append("weekly_reports")
    if not aconex:
        missing.append("aconex")

    return {
        "baseline_schedule": baseline,
        "daily_reports": daily,
        "weekly_reports": weekly,
        "aconex_connected": aconex,
        "ready": not missing,
        "missing": missing,
    }


# ── project memory / durable facts (Roadmap V2 · Epic 3) ────────────────────

def set_fact(
    project_id: str,
    key: str,
    value: str,
    source_document: Optional[str] = None,
    confidence: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Upsert a durable fact for a project (one row per project+key)."""
    _ensure_db()
    now = _now()
    with _lock:
        with SessionLocal() as session:
            existing = session.scalars(
                select(ProjectFact).where(
                    ProjectFact.project_id == project_id,
                    ProjectFact.key == key,
                )
            ).one_or_none()
            if existing:
                existing.value = str(value)
                existing.source_document = source_document
                existing.confidence = confidence
                existing.updated_at = now
            else:
                session.add(
                    ProjectFact(
                        id=str(uuid.uuid4())[:8],
                        project_id=project_id,
                        key=key,
                        value=str(value),
                        source_document=source_document,
                        confidence=confidence,
                        updated_at=now,
                    )
                )
            session.commit()
    return get_fact(project_id, key)


def get_fact(project_id: str, key: str) -> Optional[Dict[str, Any]]:
    _ensure_db()
    with SessionLocal() as session:
        fact = session.scalars(
            select(ProjectFact).where(
                ProjectFact.project_id == project_id,
                ProjectFact.key == key,
            )
        ).one_or_none()
    return _fact_as_dict(fact) if fact else None


def list_facts(project_id: str) -> List[Dict[str, Any]]:
    _ensure_db()
    with SessionLocal() as session:
        rows = session.scalars(
            select(ProjectFact)
            .where(ProjectFact.project_id == project_id)
            .order_by(ProjectFact.key)
        ).all()
    return [_fact_as_dict(fact) for fact in rows]


def search_facts(project_id: str, query: str) -> List[Dict[str, Any]]:
    """Keyword search over fact keys + values (case-insensitive, any-term)."""
    facts = list_facts(project_id)
    terms = (query or "").lower().split()
    if not terms:
        return facts
    return [
        f for f in facts
        if any(t in f"{f['key']} {f['value']}".lower() for t in terms)
    ]


def delete_fact(project_id: str, key: str) -> bool:
    _ensure_db()
    with _lock:
        with SessionLocal() as session:
            fact = session.scalars(
                select(ProjectFact).where(
                    ProjectFact.project_id == project_id,
                    ProjectFact.key == key,
                )
            ).one_or_none()
            if not fact:
                return False
            session.delete(fact)
            session.commit()
            return True
