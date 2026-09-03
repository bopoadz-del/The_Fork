"""Project entity — groups documents and gates project-level analytics.

Roadmap V2 · Part 0.1 (Project entity) + 0.2 (readiness gate).

A Project is the backbone the platform was missing: documents are no longer
processed in isolation, and project-level analytics (progress tracking, earned
value) stay inert until the project is genuinely set up.

SQLAlchemy-backed via app.core.db — unified The Fork schema.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, func, or_, select, text as sqla_text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.db import SessionLocal, engine, get_database_url
from app.core.models import Document, IngestionJob, Project, ProjectFact

import logging

logger = logging.getLogger(__name__)

# ── pilot master-corpus alias ───────────────────────────────────────────────
# Per-project Drive approval/indexing is not pilot-ready. Expose the existing
# full-drive corpus (currently stored under project_id "projects_folder") as a
# single admin-visible pilot project without duplicating chunks or re-importing
# Drive. This is a temporary alias; proper per-project trees come post-pilot.
MASTER_CORPUS_PROJECT_ID = os.getenv("MASTER_CORPUS_PROJECT_ID", "master_corpus")
MASTER_CORPUS_SOURCE_PROJECT_ID = os.getenv(
    "MASTER_CORPUS_SOURCE_PROJECT_ID", "projects_folder"
)
MASTER_CORPUS_NAME = os.getenv("MASTER_CORPUS_NAME", "Master Corpus")


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
# Tracks WHICH database the schema was created in. `_initialized` alone is a
# global boolean, but the database is per-DATA_DIR / per-DATABASE_URL: after
# init runs against one database, `_ensure_db` short-circuits for every other
# one, so a later switch silently gets NO schema ("no such table: projects").
# Sibling stores (doc_index, hydration_store, rag.budget) already track the
# URL; these did not. Found via an order-dependent test failure, but the bug
# is real wherever the database can change after first use.
_initialized_for_url: str | None = None


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
        "location": getattr(project, "location", None),
        "status": project.status,
        "aconex_connected": bool(project.aconex_connected),
        "user_id": project.user_id,
        "created_at": project.created_at,
        "is_approved": bool(getattr(project, "is_approved", True)),
        "origin": getattr(project, "origin", "user_create") or "user_create",
        "is_master_corpus": False,
    }


# Pointer aliases actually written by P1B / rag_render / Drive OAuth / audit.
# #445 only read ``r2_object_key`` + ``drive_file_id`` on a dict. Live rows
# also use camelCase, a JSON *string* in the JSONB column, and rag_render
# keys (drive_file_id without an R2 key).
_R2_KEY_ALIASES = (
    "r2_object_key", "r2_key", "object_key", "r2ObjectKey", "r2Key",
)
_DRIVE_ID_ALIASES = (
    "drive_file_id", "driveFileId", "drive_id", "driveId",
)
_R2_NEST_KEYS = ("r2_archive", "archive", "r2")


def coerce_document_metadata(meta: Any) -> Dict[str, Any]:
    """Return metadata as a dict. Live JSONB sometimes stores a JSON string."""
    if meta is None:
        return {}
    if isinstance(meta, dict):
        return meta
    if isinstance(meta, str):
        try:
            parsed = json.loads(meta)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
        # Double-encoded JSONB string (audit rows, CAST-as-text inserts).
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}
    return {}


def _first_nonempty_str(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def extract_document_source_pointers(doc: Dict[str, Any]) -> Dict[str, str]:
    """Resolve R2 / Drive pointers from a document row.

    Reads the same keys P1B Drive ingest writes
    (``scripts/p1b_ingest_drive_server.py`` ``common_meta``):
    ``drive_file_id``, ``r2_object_key``, ``r2_bucket``, ``r2_endpoint``.
    Also accepts rag_render / audit aliases so a RAG-citable row without
    the exact #445 names still hydrates.
    """
    meta = coerce_document_metadata(doc.get("metadata"))
    nested: Dict[str, Any] = {}
    for nest_key in _R2_NEST_KEYS:
        raw = meta.get(nest_key)
        if isinstance(raw, dict):
            nested.update(raw)

    r2_key = _first_nonempty_str(
        doc.get("r2_object_key"),
        *[meta.get(alias) for alias in _R2_KEY_ALIASES],
        *[nested.get(alias) for alias in _R2_KEY_ALIASES],
    )
    drive_id = _first_nonempty_str(
        doc.get("drive_file_id"),
        *[meta.get(alias) for alias in _DRIVE_ID_ALIASES],
        *[nested.get(alias) for alias in _DRIVE_ID_ALIASES],
    )
    r2_bucket = _first_nonempty_str(
        doc.get("r2_bucket"), meta.get("r2_bucket"), nested.get("r2_bucket"),
    )
    return {
        "r2_object_key": r2_key,
        "drive_file_id": drive_id,
        "r2_bucket": r2_bucket,
    }


def _path_looks_present(path: str) -> bool:
    return bool(path) and os.path.isfile(path) and os.path.getsize(path) > 0


def _document_as_dict(document: Document) -> Dict[str, Any]:
    out = {
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
    meta = coerce_document_metadata(getattr(document, "metadata_", None))
    if getattr(document, "metadata_", None) is not None:
        out["metadata"] = meta
    pointers = extract_document_source_pointers(out)
    local = _path_looks_present(out.get("file_path") or "")
    out["has_remote_source"] = bool(
        pointers["r2_object_key"] or pointers["drive_file_id"]
    )
    out["has_file"] = local or out["has_remote_source"]
    return out


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
    global _initialized, _initialized_for_url
    with _lock:
        from app.core.users import init_db as init_users_db

        init_users_db()
        _ensure_sqlite_parent_dir()
        Project.__table__.create(bind=engine, checkfirst=True)
        Document.__table__.create(bind=engine, checkfirst=True)
        ProjectFact.__table__.create(bind=engine, checkfirst=True)
        IngestionJob.__table__.create(bind=engine, checkfirst=True)
        _patch_legacy_columns()
        _initialized = True
        _initialized_for_url = get_database_url()


def _patch_legacy_columns() -> None:
    """Add columns to legacy tables when they're missing.

    SQLite only — Postgres deployments are managed by Alembic. This
    function exists because checkfirst=True on Table.create() does NOT
    add new columns to existing tables; we have to ALTER manually for
    dev environments + tests.

    Currently handles:
      * projects.is_approved (Alembic 0004)
      * projects.origin       (Alembic 0005)
      * documents.metadata    (Alembic 0009)
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
            if "location" not in cols:  # 0010 (F4 — daily_site_report weather)
                conn.execute(sqla_text(
                    "ALTER TABLE projects ADD COLUMN location TEXT"
                ))
                conn.commit()

            doc_cols = {row[1] for row in conn.execute(
                sqla_text("PRAGMA table_info(documents)")
            )}
            if "metadata" not in doc_cols:
                conn.execute(sqla_text(
                    "ALTER TABLE documents ADD COLUMN metadata TEXT"
                ))
                conn.commit()
    except Exception:
        # Don't crash boot on a dev-environment patch failure — the
        # next call to a feature that needs the column will surface
        # the real error with a clearer stack.
        import logging
        logging.getLogger(__name__).warning(
            "legacy column patch skipped", exc_info=True,
        )


def _ensure_db() -> None:
    if not _initialized or _initialized_for_url != get_database_url():
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

def _stable_project_id_for_name(name: str) -> str:
    """Deterministic id so parallel ingest workers converge without UNIQUE(name)."""
    digest = hashlib.sha256(name.strip().lower().encode("utf-8")).hexdigest()[:16]
    return f"p_{digest}"


def _find_active_project_by_name(name: str) -> Optional[Dict[str, Any]]:
    """Return the oldest active project with this exact name, if any."""
    _ensure_db()
    with SessionLocal() as session:
        row = session.execute(
            select(Project)
            .where(Project.name == name)
            .where(Project.status != "archived")
            .order_by(Project.created_at.asc())
            .limit(1)
        ).scalar_one_or_none()
    if not row:
        return None
    return _project_as_dict(row)


def create_project(
    name: str,
    client: Optional[str] = None,
    user_id: str = "system",
    *,
    location: Optional[str] = None,
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
                    location=(location or "").strip() or None,
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


def set_project_location(project_id: str, location: Optional[str]) -> Optional[Dict[str, Any]]:
    """Set (or clear, with None) a project's confirmed site location. Returns the
    updated project dict, or None if the project does not exist."""
    _ensure_db()
    with _lock:
        with SessionLocal() as session:
            project = session.get(Project, project_id)
            if project is None:
                return None
            project.location = (location or "").strip() or None
            session.commit()
    return get_project(project_id)


def get_or_create_project(
    name: str,
    client: Optional[str] = None,
    user_id: str = "system",
    *,
    is_approved: bool = True,
    project_id: Optional[str] = None,
    origin: str = "user_create",
) -> Tuple[Dict[str, Any], bool]:
    """Ensure exactly one project row for this ingest target (id-primary).

    ``projects.name`` is not UNIQUE, so parallel shard workers that each call
    ``create_project(folder_name)`` would mint separate random ids and split
    the corpus. This helper:

    1. Uses a stable ``project_id`` (caller-supplied, else derived from name).
    2. Returns an existing row by id, else by name (oldest active — resume).
    3. Creates with that primary key; on ``IntegrityError`` re-fetches.

    Returns ``(project_dict, created)``.
    """
    _ensure_db()
    pid = (project_id or "").strip() or _stable_project_id_for_name(name)

    existing = get_project(pid)
    if existing:
        return existing, False

    by_name = _find_active_project_by_name(name)
    if by_name:
        # Legacy / pre-shard rows: keep ingesting into the oldest name match
        # rather than creating a second project under the stable id.
        return by_name, False

    try:
        created = create_project(
            name,
            client=client,
            user_id=user_id,
            is_approved=is_approved,
            project_id=pid,
            origin=origin,
        )
        return created, True
    except IntegrityError:
        # Another worker won the insert race on the same primary key.
        raced = get_project(pid) or _find_active_project_by_name(name)
        if raced is None:
            raise
        return raced, False


def list_projects(
    user_id: Optional[str] = None,
    *,
    include_admin_approved: bool = False,
    include_hidden: bool = False,
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
    the virtual ``master_corpus`` alias is appended to the list.
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
        # Layered RAG (Stage 5): hidden rows (RAG corpora / GK / eval) stay
        # retrievable but drop out of the sidebar. include_hidden=True is for
        # internal enumeration / admin tooling only.
        if not include_hidden:
            stmt = stmt.where(Project.hidden_from_sidebar.is_(False))
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
    doc_limit: Optional[int] = None,
    doc_offset: int = 0,
) -> Optional[Dict[str, Any]]:
    """Load a project the caller can access.

    PR D — non-owners may also read admin-approved platform projects
    when ``include_admin_approved=True``. ``is_approved=False`` rows
    stay owner-only regardless of origin (defensive — admins shouldn't
    leak detected-but-pending candidates to users).

    Pilot: a virtual master-corpus project (default ``master_corpus``)
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
    # Paginate documents: a large corpus (2713-doc master) must not serialize
    # every row on every load. doc_limit=None preserves the legacy "all docs"
    # behaviour for the many internal callers; the detail endpoint passes a
    # first-page limit. document_count is a cheap COUNT, NOT len() of the page.
    proj["documents"] = list_documents(
        source_id,
        limit=doc_limit,
        offset=doc_offset,
        newest_first=doc_limit is not None,
    )
    proj["document_count"] = count_documents(source_id)
    proj["readiness"] = compute_readiness(source_id)
    return proj


def project_owner(project_id: str) -> Optional[str]:
    """Return the user_id that owns the project, or None if the project doesn't exist."""
    _ensure_db()
    with SessionLocal() as session:
        project = session.get(Project, project_id)
    return project.user_id if project else None


def _is_platform_project(project: Dict[str, Any]) -> bool:
    """A platform/corpus project an admin may read cross-tenant on the chat/RAG
    DATA path: system/seed-owned, an admin-approved shared project, or the
    master-corpus alias. A real end-user's private ``user_create`` project is
    NOT platform — it must never be readable by another account here.
    """
    from app.core.users import SYSTEM_USER_ID
    return (
        project.get("user_id") == SYSTEM_USER_ID
        or (project.get("origin") or "user_create") == "admin_drive_approved"
        or bool(project.get("is_master_corpus"))
    )


def get_project_accessible(project_id: str, user_id: Optional[str] = None):
    """Open-access resolution for READ/chat surfaces (2026-07-26).

    The chat tenant gates used owner-only get_project(pid, user_id=...),
    which silently DROPPED the project for admins and for admin-approved
    shared platform projects -- the caller could OPEN the project in the UI
    (#267 read rule) and upload to it (#277) but chat lost all RAG context
    on it (zero injected sources, no-op search tool). Third instance of the
    same asymmetry class; this helper is the single rule for all of them:
    owner -> admin-approved shared -> admin on PLATFORM projects only.
    Returns the project dict or None; archived projects stay invisible on
    every path.

    SECURITY (legacy-admin tenancy fix): ``require_user`` maps a legacy
    API key to the singleton ``system`` user for *identity* only. Authority
    (``role``) comes from the key record — only ``CEREBRUM_MASTER_KEY`` is
    minted with ``role="admin"``. A plain API key must not inherit admin
    from the system user row. The admin fallthrough below is therefore
    reachable by genuine admins and the master key, not every legacy key.
    The admin cross-tenant read is therefore scoped to PLATFORM projects
    (``_is_platform_project``) — system/seed-owned corpora and admin-approved
    shared projects — and NEVER a real end-user's private project. Genuine
    admin cross-tenant operations on arbitrary projects go through the
    explicit, audited ``/v1/admin/*`` endpoints (which call ``get_project``
    directly), not this chat data-path helper. Owner reads and the
    admin-approved-shared grant are unchanged (handled by the scoped
    ``get_project`` call above).
    """
    if not user_id:
        # user_id=None means UNSCOPED in get_project — an anonymous caller
        # must never resolve a project through this helper (fail closed).
        return None
    proj = get_project(project_id, user_id=user_id,
                       include_admin_approved=True, doc_limit=0)
    if proj is not None:
        return proj
    try:
        from app.core import users as users_store
        u = users_store.get_user_by_id(user_id)
        if u and (u.get("role") or "").lower() == "admin":
            # Admin fallthrough — but ONLY for platform/corpus projects, so a
            # legacy-key SYSTEM admin (or any promoted admin) cannot read a
            # real user's private project through the chat gate.
            candidate = get_project(project_id, doc_limit=0)
            if candidate is not None and _is_platform_project(candidate):
                return candidate
    except Exception:  # noqa: BLE001 -- fail closed on lookup errors
        return None
    return None


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


def set_hidden_from_sidebar(project_id: str, hidden: bool = True) -> bool:
    """Hide (or unhide) a project from the sidebar WITHOUT archiving it — the
    row and its RAG chunks are untouched and stay retrievable. Returns False if
    the project doesn't exist. Reversible: pass hidden=False to restore."""
    _ensure_db()
    with _lock:
        with SessionLocal() as session:
            project = session.get(Project, project_id)
            if not project:
                return False
            project.hidden_from_sidebar = bool(hidden)
            session.commit()
            return True


def approve_project(project_id: str) -> bool:
    """Make an EXISTING project admin-visible: set ``is_approved=True`` and
    ``origin='admin_drive_approved'`` so the sidebar (which filters on that
    origin) lists it for all users — the same visibility a Drive-approved
    project gets, without re-importing. Idempotent. Returns False if not found.
    """
    _ensure_db()
    with _lock:
        with SessionLocal() as session:
            project = session.get(Project, project_id)
            if not project:
                return False
            project.is_approved = True
            project.origin = "admin_drive_approved"
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


# Projects that may NEVER be hard-purged regardless of status — the master
# corpus, its backing source, and the shared general-knowledge base.
_PURGE_PROTECTED_IDS = {
    MASTER_CORPUS_PROJECT_ID,
    MASTER_CORPUS_SOURCE_PROJECT_ID,
    os.getenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", "training_material").split(",")[0].strip(),
}


def purge_archived_project(project_id: str) -> str:
    """PERMANENTLY remove an ARCHIVED project — its RAG chunks AND its row.

    SAFETY (never-delete-RAG rule still holds for everything live):
      * refuses protected master/backing/general-knowledge ids outright;
      * refuses any project whose status is not 'archived' — so active
        projects and the master corpus can never be purged through here.
    Returns one of: 'protected' | 'not_found' | 'not_archived' | 'purged'.
    """
    if project_id in _PURGE_PROTECTED_IDS:
        return "protected"
    _ensure_db()
    with _lock:
        with SessionLocal() as session:
            project = session.get(Project, project_id)
            if not project:
                return "not_found"
            if getattr(project, "status", "active") != "archived":
                return "not_archived"
    # Clear the vector store first, then hard-delete the row (cascade clears
    # the chunks table). Both, belt-and-suspenders.
    try:
        from app.core import doc_index
        doc_index.purge_project_index(project_id)
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            "purge_archived_project: index purge failed for %s: %s", project_id, exc)
    delete_project(project_id)
    return "purged"


def set_aconex(project_id: str, connected: bool) -> bool:
    """Mark whether this project has a CDE feed (live OAuth or managed outside)."""
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

def storage_project_id(project_id: str) -> str:
    """The project id rows must actually be WRITTEN under.

    ``MASTER_CORPUS_PROJECT_ID`` is a VIRTUAL alias: it is injected into
    listings on the fly and deliberately has no ``projects`` row (see
    ``_is_master_corpus_alias``). Every reader already resolves it —
    ``get_project``, ``list_documents``, ``count_documents`` and friends all
    open with ``_master_corpus_source(project_id) or project_id``.

    ``add_document`` did not, and it is a WRITER. So uploading to the Master
    Corpus passed the permission check (the reader resolved the alias and found
    the backing corpus) and then tried to insert a Document row whose
    ``project_id`` foreign key pointed at an id with no project behind it:

        sqlite3.IntegrityError: FOREIGN KEY constraint failed

    An unhandled exception mid-request means the response never completes, and
    a request that dies without a response is reported by the browser as
    ``TypeError: Failed to fetch`` — with no status, because there is no
    response to read a status from. Uploading to the Master Corpus was
    therefore impossible, and said so in the least diagnosable way available.

    Readers may keep using the alias; only writes need resolving.
    """
    return _master_corpus_source(project_id) or project_id


def resolve_document_size(size: int, file_path: Optional[str]) -> int:
    """The size to record for a document — measured from disk when not supplied.

    Live incident: the Master Corpus listed every one of its ~227 documents as
    "0 B" in the UI. ``size`` defaulted to 0 and NOTHING ever checked it against
    the bytes actually on disk, so a caller that forgot the argument (or a
    migration payload whose transform dropped the field) silently registered a
    document whose recorded size was a lie. The UI rendered that lie faithfully.

    Policy: a caller-supplied positive size wins (it is the plaintext length the
    writer just measured, and is correct even when the file is encrypted at
    rest). Otherwise, if a readable ``file_path`` exists, measure it. If the
    file is missing or unreadable the size stays 0 — a metadata-only row is a
    legitimate thing to register (bulk-inserted corpora reference files that
    live on an operator's drive, not ours) — but it is logged, because a
    document that claims zero bytes is nearly always a defect upstream.
    """
    try:
        if int(size or 0) > 0:
            return int(size)
    except (TypeError, ValueError):
        pass  # unparsable size is treated as "not supplied"
    if not file_path:
        return 0
    try:
        from app.core import file_crypto

        measured = file_crypto.plaintext_size(file_path)
        if measured > 0:
            return measured
        logger.warning(
            "document at %s measures 0 bytes on disk — registering size 0",
            file_path,
        )
        return 0
    except (OSError, ValueError) as exc:
        logger.warning(
            "could not measure size of %s (%s); registering size 0", file_path, exc,
        )
        return 0


def add_document(
    project_id: str,
    original_name: str,
    stored_as: Optional[str] = None,
    file_path: Optional[str] = None,
    size: int = 0,
    role: Optional[str] = None,
    content_sha256: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Register a document under a project. Storing only — runs no analysis."""
    _ensure_db()
    # Writes go to the BACKING corpus, never the virtual alias — see
    # storage_project_id. Without this, uploading to the Master Corpus violated
    # the project_id foreign key and killed the request.
    project_id = storage_project_id(project_id)
    # A recorded size must describe the bytes that exist, not the default of
    # whichever caller forgot to pass one — see resolve_document_size.
    size = resolve_document_size(size, file_path)
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
                    metadata_=metadata,
                )
            )
            session.commit()
    with SessionLocal() as session:
        document = session.get(Document, did)
    assert document is not None
    return _document_as_dict(document)


def create_ingestion_job(project_id: str, document_id: str):
    """Persist a pending ingestion job for the worker queue.

    Returns the created job row so callers can pass ``job.id`` to the queue.
    """
    from app.core.db import SessionLocal
    from app.core.models import IngestionJob

    _ensure_db()
    # Same alias-vs-row hazard as add_document: IngestionJob.project_id is a
    # foreign key, and the alias has no row to point at.
    project_id = storage_project_id(project_id)
    db = SessionLocal()
    try:
        job = IngestionJob(project_id=project_id, document_id=document_id, status="pending")
        db.add(job)
        db.commit()
        db.refresh(job)
        return job
    finally:
        db.close()


def set_document_size_if_zero(doc_id: str, size: int) -> None:
    """Record ``size`` only when the row still claims 0 bytes.

    Master Corpus / P1B rows often land as ``size=0`` after the local file is
    deleted (or never existed). Once preview hydrates real bytes we know the
    length — write it so the UI stops rendering "0 B" / "no file".
    """
    if int(size or 0) <= 0 or not doc_id:
        return
    _ensure_db()
    with _lock:
        with SessionLocal() as session:
            document = session.get(Document, doc_id)
            if document is None or int(document.size or 0) > 0:
                return
            document.size = int(size)
            session.commit()


def _preview_cache_path(doc: Dict[str, Any]) -> str:
    data_dir = os.getenv("DATA_DIR", "./data")
    cache_dir = os.path.join(data_dir, "preview_cache")
    os.makedirs(cache_dir, exist_ok=True)
    ext = os.path.splitext((doc.get("original_name") or "").lower())[1][:20]
    raw_id = "".join(
        c for c in str(doc.get("id") or "unknown") if c.isalnum() or c in "-_"
    )[:32] or "unknown"
    return os.path.join(cache_dir, f"{raw_id}{ext}")


def _local_plaintext_size(path: str) -> int:
    if not path or not os.path.exists(path):
        return 0
    try:
        from app.core import file_crypto

        return int(file_crypto.plaintext_size(path) or 0)
    except (OSError, ValueError):
        return 0


def _resolve_drive_id_by_filename(doc: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Fill a missing drive_file_id from an exact Drive filename match.

    Live Master Corpus cites (e.g. ``ocr1exec``) were inserted by
    ``rag_render_bulk_ingest`` with ``drive_file_id: null`` and a stale
    Windows ``file_path``. The PDF still exists on Drive under
    ``original_name``. Persist the id on success so later hydrates skip
    the lookup.
    """
    name = str(doc.get("original_name") or "").strip()
    if not name:
        return None, "document has no original_name"
    try:
        from app.core import gdrive_service

        file_id, err = gdrive_service.find_file_id_by_exact_name(name)
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"
    if not file_id:
        return None, err or f"no Drive file named {name}"
    _persist_resolved_drive_id(doc, file_id, "original_name")
    logger.info(
        "resolved drive_file_id for doc %s from filename %r",
        doc.get("id"), name,
    )
    return file_id, None


def _persist_resolved_drive_id(
    doc: Dict[str, Any], file_id: str, source: str,
) -> None:
    """Write ``drive_file_id`` onto the row after a successful resolve."""
    doc_id = str(doc.get("id") or "")
    if not doc_id or not file_id:
        return
    try:
        update_document_metadata(doc_id, {
            "drive_file_id": file_id,
            "drive_resolved_from": source,
        })
    except Exception:
        logger.info(
            "could not persist resolved drive_file_id for doc %s",
            doc_id, exc_info=True,
        )


def _reconstruct_r2_key(doc: Dict[str, Any], drive_id: str) -> str:
    """P1B key layout when the row recorded Drive id but not the object key."""
    if not drive_id:
        return ""
    from app.core import r2_storage

    project_id = str(doc.get("project_id") or "").strip()
    name = str(doc.get("original_name") or "").strip()
    if not project_id or not name:
        return ""
    return r2_storage.object_key_for(project_id, drive_id, name)


def _fetch_remote_document_bytes(
    doc: Dict[str, Any],
) -> Tuple[Optional[bytes], Optional[str], Optional[str]]:
    """R2 first (P1B source of truth), then Drive if a file id is recorded.

    Returns ``(bytes, source, error)``. ``error`` is a logging-safe reason
    when bytes are missing — never a secret.
    """
    from app.core import r2_storage

    pointers = extract_document_source_pointers(doc)
    key = pointers["r2_object_key"]
    drive_id = pointers["drive_file_id"]
    bucket = pointers["r2_bucket"] or None
    r2_error: Optional[str] = None
    drive_error: Optional[str] = None

    keys_to_try: List[str] = []
    if key:
        keys_to_try.append(key)
    if not drive_id:
        # rag_backfill_client_clean_all stubs: size=0, G:\ path, null
        # drive_file_id, no r2_object_key. Resolve the live Drive file by
        # exact original_name and persist the id so the next preview is cheap.
        resolved_id, resolve_err = _resolve_drive_id_by_filename(doc)
        if resolved_id:
            drive_id = resolved_id
        elif resolve_err:
            drive_error = resolve_err

    reconstructed = _reconstruct_r2_key(doc, drive_id)
    if reconstructed and reconstructed not in keys_to_try:
        keys_to_try.append(reconstructed)

    for candidate in keys_to_try:
        try:
            blob = r2_storage.fetch_object_bytes(candidate, bucket=bucket)
        except TypeError:
            # Tests / older mocks only accept the key.
            blob = r2_storage.fetch_object_bytes(candidate)
        if blob:
            return blob, "r2", None
        r2_error = r2_storage.fetch_failure_reason(candidate, bucket=bucket)
        logger.info(
            "R2 hydrate miss for doc %s key_tail=%s reason=%s",
            doc.get("id"), candidate.rsplit("/", 1)[-1], r2_error,
        )

    if drive_id:
        try:
            from app.core import gdrive_service

            # files.get (supportsAllDrives) confirms the id before media.
            # Name-search cannot see anyone-with-link files; files.get can.
            _meta, meta_err = gdrive_service.get_file_metadata(drive_id)
            if meta_err:
                logger.info(
                    "Drive files.get miss for doc %s: %s", doc.get("id"), meta_err,
                )
            blob, err = gdrive_service.download_file_bytes(drive_id)
            if blob:
                return blob, "drive", None
            drive_error = err or "Drive download returned no bytes"
            logger.info(
                "Drive fallback failed for doc %s: %s", doc.get("id"), drive_error,
            )
            # SA media failed (or SA is blind). Anyone-with-link files are
            # still fetchable at the public uc/download URL. Private files
            # fail closed — the helper never uses the SA token.
            pub, pub_err = gdrive_service.download_public_file_bytes(drive_id)
            if pub:
                _persist_resolved_drive_id(doc, drive_id, "public_download")
                return pub, "drive_public", None
            if pub_err:
                drive_error = f"{drive_error}; {pub_err}"
                logger.info(
                    "public Drive download failed for doc %s: %s",
                    doc.get("id"), pub_err,
                )
        except Exception as exc:  # noqa: BLE001
            drive_error = f"{type(exc).__name__}: {exc}"
            logger.info(
                "Drive fallback skipped for doc %s: %s", doc.get("id"), drive_error,
            )

    if not key and not drive_id:
        extra = f"; {drive_error}" if drive_error else ""
        return None, None, (
            "no R2 object key or Drive file id on this document" + extra
        )
    parts = [p for p in (r2_error, drive_error) if p]
    if parts:
        return None, None, "; ".join(parts)
    if drive_id:
        return None, None, "Drive fallback failed"
    return None, None, "R2 object missing or fetch failed"


def materialize_document_file(doc: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """Resolve bytes for a document row onto a readable local path.

    Master Corpus / P1B ingest archives to R2 and deletes the local file, so
    ``file_path`` is often a stale path and ``size`` is 0 even though the
    object exists. Order:

    1. Existing local ``file_path`` with plaintext length > 0
    2. On-demand preview cache from a prior hydrate
    3. R2 key (row, metadata aliases, or reconstructed P1B layout)
    4. Drive ``drive_file_id`` / ``driveFileId`` (R2 miss / archive failed)
    5. Public Drive uc/download when the id is anyone-with-link and SA media fails

    Returns ``(path, "ok")`` or ``(None, "empty"|reason)``. Reason is a
    logging-safe phrase the router appends to the 404 — never a 500.
    """
    from app.core import file_crypto

    fp = doc.get("file_path") or ""
    if _local_plaintext_size(fp) > 0:
        return fp, "ok"

    cache = _preview_cache_path(doc)
    if _local_plaintext_size(cache) > 0:
        return cache, "ok"

    raw, source, fetch_error = _fetch_remote_document_bytes(doc)
    if raw is None:
        if _local_plaintext_size(fp) == 0 and fp and os.path.exists(fp):
            return None, "empty"
        return None, fetch_error or "missing"
    if len(raw) == 0:
        return None, "empty"

    os.makedirs(os.path.dirname(cache), exist_ok=True)
    file_crypto.write_document(cache, raw)
    logger.info(
        "hydrated document %s from %s (%s bytes) for preview",
        doc.get("id"), source, len(raw),
    )
    if int(doc.get("size") or 0) <= 0:
        set_document_size_if_zero(str(doc.get("id") or ""), len(raw))
    return cache, "ok"


def update_document_metadata(doc_id: str, metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Merge ``metadata`` into the document's existing metadata_ column.

    Returns the updated document dict, or None if the document was not found.
    """
    _ensure_db()
    with _lock:
        with SessionLocal() as session:
            document = session.get(Document, doc_id)
            if document is None:
                return None
            # Copy — SQLAlchemy JSON does not flag in-place mutation of the
            # same dict object, so a stub row would keep drive_file_id=null.
            current = dict(coerce_document_metadata(document.metadata_))
            current.update(metadata)
            document.metadata_ = current
            session.commit()
    with SessionLocal() as session:
        document = session.get(Document, doc_id)
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


def documents_matching_filename_terms(
    project_id: str,
    terms: List[str],
    *,
    min_terms: int = 2,
    require_letter: bool = False,
    limit: int = 8,
) -> List[Dict[str, str]]:
    """Documents whose ``original_name`` or ``file_path`` overlap ``terms``.

    Used by letter/signatory retrieval so a named-site letter (the UBCC /
    Wadi Safar completion letter lives in ``Misc/`` with the site and
    party in the filename) can be found without listing the whole corpus.
    ``terms`` are already sanitised by the retriever (alphanumeric, ≥4
    chars); LIKE wildcards are therefore not attacker-controlled.

    Returns ``[{id, original_name, file_path}, ...]`` ranked by overlap
    descending, capped at ``limit``. Empty when nothing qualifies.
    """
    _ensure_db()
    source_id = _master_corpus_source(project_id) or project_id
    cleaned: List[str] = []
    seen: set[str] = set()
    for raw in terms or []:
        tok = (raw or "").strip().lower()
        if len(tok) < 3 or tok in seen:
            continue
        if any(ch in tok for ch in "%_"):
            continue
        seen.add(tok)
        cleaned.append(tok)
        if len(cleaned) >= 8:
            break
    if not cleaned or not source_id:
        return []

    name_l = func.lower(Document.original_name)
    path_l = func.lower(func.coalesce(Document.file_path, ""))
    term_clauses = [
        or_(name_l.like(f"%{tok}%"), path_l.like(f"%{tok}%"))
        for tok in cleaned
    ]
    stmt = (
        select(Document.id, Document.original_name, Document.file_path)
        .where(Document.project_id == source_id)
        .where(or_(*term_clauses))
    )
    if require_letter:
        stmt = stmt.where(or_(name_l.like("%letter%"), path_l.like("%letter%")))
    stmt = stmt.limit(80)

    try:
        with SessionLocal() as session:
            rows = session.execute(stmt).all()
    except Exception:
        logger.warning(
            "documents_matching_filename_terms failed for %s",
            project_id,
            exc_info=True,
        )
        return []

    scored: List[Tuple[int, Dict[str, str]]] = []
    for row in rows:
        blob = f"{row.original_name or ''} {row.file_path or ''}".lower()
        hits = sum(1 for tok in cleaned if tok in blob)
        if hits < min_terms:
            continue
        scored.append((
            hits,
            {
                "id": row.id,
                "original_name": row.original_name or "",
                "file_path": row.file_path or "",
            },
        ))
    scored.sort(key=lambda item: -item[0])
    return [doc for _hits, doc in scored[:limit]]


def documents_matching_title_phrase(
    project_id: str,
    phrase: str,
    *,
    limit: int = 8,
) -> List[Dict[str, str]]:
    """Documents whose ``original_name`` or ``file_path`` contains ``phrase``.

    Used by specification-title retrieval (live pack C2). The Variation
    Procedure spec is named in its filename
    (``DGDAX-DGD-PMO-SPE-012650-1.0 Variation Procedure``); cosine and
    term-rescue cannot tell that file from a demolition-spec volume that
    merely mentions variations. Exact-phrase LIKE on the upload name is
    the discriminator those volumes cannot fake.

    ``phrase`` is sanitised here (lowercased, collapsed whitespace, no
    LIKE wildcards). Empty when nothing qualifies.
    """
    _ensure_db()
    source_id = _master_corpus_source(project_id) or project_id
    cleaned = " ".join((phrase or "").lower().split())
    if (
        not cleaned
        or len(cleaned) < 8
        or " " not in cleaned
        or any(ch in cleaned for ch in "%_")
        or not source_id
    ):
        return []

    name_l = func.lower(Document.original_name)
    path_l = func.lower(func.coalesce(Document.file_path, ""))
    needle = f"%{cleaned}%"
    stmt = (
        select(Document.id, Document.original_name, Document.file_path)
        .where(Document.project_id == source_id)
        .where(or_(name_l.like(needle), path_l.like(needle)))
        .limit(max(1, int(limit or 8)))
    )
    try:
        with SessionLocal() as session:
            rows = session.execute(stmt).all()
    except Exception:
        logger.warning(
            "documents_matching_title_phrase failed for %s",
            project_id,
            exc_info=True,
        )
        return []
    return [
        {
            "id": row.id,
            "original_name": row.original_name or "",
            "file_path": row.file_path or "",
        }
        for row in rows
    ]


def list_documents(
    project_id: str,
    *,
    limit: Optional[int] = None,
    offset: int = 0,
    newest_first: bool = False,
) -> List[Dict[str, Any]]:
    """List a project's documents.

    ``limit``/``offset`` paginate (used by the workspace + the documents
    endpoint so a 2700-doc corpus doesn't serialize every row on every load).
    ``newest_first`` orders by upload time descending — the natural order for a
    paginated sidebar. Defaults preserve the legacy "all docs, oldest-first"
    behaviour so existing callers are untouched.
    """
    _ensure_db()
    source_id = _master_corpus_source(project_id) or project_id
    order = Document.uploaded_at.desc() if newest_first else Document.uploaded_at
    with SessionLocal() as session:
        stmt = (
            select(Document)
            .where(Document.project_id == source_id)
            .order_by(order)
        )
        if limit is not None:
            stmt = stmt.limit(limit).offset(offset)
        rows = session.scalars(stmt).all()
    return [_document_as_dict(document) for document in rows]


def count_documents(project_id: str) -> int:
    """Total document count for a project — a cheap indexed COUNT, so a
    paginated listing can report the total without serializing every row."""
    _ensure_db()
    source_id = _master_corpus_source(project_id) or project_id
    with SessionLocal() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(Document)
                .where(Document.project_id == source_id)
            )
            or 0
        )


def audit_document_sizes(project_id: Optional[str] = None) -> Dict[str, Any]:
    """Report documents whose recorded size does not describe reality.

    Read-only counterpart to :func:`repair_document_sizes` — an operator (or a
    test) can assert the corpus is honest without mutating it. Buckets:

    * ``zero_with_file``   — size 0 but a readable file exists (REPAIRABLE; this
      is the "0 B" corpus defect).
    * ``zero_no_file``     — size 0 and no file on disk (metadata-only row; only
      repairable by re-ingesting the source).
    * ``mismatched``       — recorded size differs from the bytes on disk.
    """
    _ensure_db()
    from app.core import file_crypto

    source_id = _master_corpus_source(project_id) or project_id
    with SessionLocal() as session:
        stmt = select(Document)
        if source_id:
            stmt = stmt.where(Document.project_id == source_id)
        rows = session.scalars(stmt).all()
        docs = [_document_as_dict(d) for d in rows]

    report: Dict[str, Any] = {
        "total": len(docs), "ok": 0,
        "zero_with_file": [], "zero_no_file": [], "mismatched": [],
    }
    for doc in docs:
        recorded = int(doc.get("size") or 0)
        path = doc.get("file_path") or ""
        actual: Optional[int] = None
        if path:
            try:
                actual = file_crypto.plaintext_size(path)
            except (OSError, ValueError):
                actual = None
        entry = {"id": doc.get("id"), "original_name": doc.get("original_name"),
                 "recorded_size": recorded, "actual_size": actual}
        if recorded <= 0:
            (report["zero_with_file"] if actual and actual > 0
             else report["zero_no_file"]).append(entry)
        elif actual is not None and actual != recorded:
            report["mismatched"].append(entry)
        else:
            report["ok"] += 1
    report["repairable"] = len(report["zero_with_file"]) + len(report["mismatched"])
    return report


def repair_document_sizes(
    project_id: Optional[str] = None, *, dry_run: bool = False,
) -> Dict[str, Any]:
    """Recompute recorded sizes from the bytes on disk.

    Repairs rows already written by the pre-fix code paths (the 0 B corpus).
    ``dry_run`` reports what WOULD change without writing — the default is to
    write, because a size that disagrees with disk is never the correct value.
    Rows whose file is missing are reported, never guessed at.
    """
    audit = audit_document_sizes(project_id)
    targets = audit["zero_with_file"] + audit["mismatched"]
    repaired = 0
    if not dry_run and targets:
        with _lock:
            with SessionLocal() as session:
                for entry in targets:
                    document = session.get(Document, entry["id"])
                    if document is None or not entry.get("actual_size"):
                        continue
                    document.size = int(entry["actual_size"])
                    repaired += 1
                session.commit()
    return {
        "dry_run": dry_run,
        "scanned": audit["total"],
        "repaired": repaired,
        "repairable": audit["repairable"],
        "unrepairable_no_file": len(audit["zero_no_file"]),
        "details": {k: audit[k] for k in
                    ("zero_with_file", "zero_no_file", "mismatched")},
    }


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

    If the chunks table has never been created (e.g. a fresh dev/test DB
    with the embedding stack not yet initialized), the explicit delete is
    a no-op — there are no chunks to remove.
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
                    # Delete RAG chunks from the active namespace. The vector
                    # store knows which namespace (e.g. v2) is current; the
                    # static RagChunk model only covers the legacy ``chunks``
                    # table and would leave chunks in a namespaced table behind.
                    try:
                        from app.core.rag import retriever as _rag
                        from app.core.rag import vector_store as _vs

                        if _rag.available():
                            store = _vs.get_store()
                            store.delete_doc(project_id, doc_id)
                        else:
                            # RAG stack not available (fresh test DB, missing
                            # model, etc.) — legacy table cleanup, best effort.
                            from app.core.models import RagChunk  # local: avoid circular
                            try:
                                session.execute(
                                    delete(RagChunk).where(
                                        RagChunk.project_id == project_id,
                                        RagChunk.doc_id == doc_id,
                                    )
                                )
                            except OperationalError as exc:
                                if "no such table" not in str(exc).lower():
                                    raise
                    except Exception:
                        # Document row deletion must never be blocked by RAG
                        # cleanup; the chunks will become unreachable once the
                        # document row is gone anyway.
                        logger.warning(
                            "swallowed %s in delete_document() — continuing",
                            "Exception", exc_info=True,
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
    # Readiness only needs each doc's role — NOT the full serialized rows.
    # Loading all documents here (via list_documents) meant get_project paid the
    # full doc-load TWICE (~8s on the 2713-doc master corpus). Select just the
    # doc_role column instead.
    with SessionLocal() as session:
        roles = list(
            session.scalars(
                select(Document.doc_role).where(Document.project_id == source_id)
            ).all()
        )
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
