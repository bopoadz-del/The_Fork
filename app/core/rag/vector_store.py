"""Per-project chunk store backed by SQLAlchemy.

Storage shape: one row per chunk in the ``chunks`` table. On PostgreSQL
embeddings use ``pgvector`` ``vector(256)`` with cosine-distance ANN search
(``ORDER BY embedding <=> :q`` scoped by ``project_id``). On SQLite the
same table stores float32 BLOBs and search falls back to numpy cosine
similarity over the project's rows — slower but works everywhere.

Hybrid retrieval: when ``RAG_HYBRID_SEARCH`` is truthy and the caller
supplies ``query_text``, the store also runs a BM25 leg and fuses with
Reciprocal Rank Fusion. On PostgreSQL the BM25 leg is ``ts_rank`` over
the ``text_search`` tsvector column + GIN index (added by Alembic 0003).
On SQLite the BM25 leg is FTS5 over a ``chunks_fts`` external-content
virtual table maintained by AFTER INSERT/DELETE/UPDATE triggers on
``chunks``. See ``_ensure_fts5_sqlite`` for the trigger rationale.

SQLAlchemy-backed via app.core.db — unified The Fork schema.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
from pgvector.sqlalchemy import Vector
from sqlalchemy import cast, delete, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

import json as json_lib

from app.core.db import _engine_for_url, _session_factory_for_url, get_database_url
from app.core.models import EMBEDDING_DIM, Document, Project
from app.core.models import make_rag_chunk_class, rag_chunk_table_name
from app.core.rag.embeddings import get_embedder

logger = logging.getLogger(__name__)


# ── Hybrid retrieval constants ────────────────────────────────────────────

# Reciprocal Rank Fusion constant per Cormack et al. 2009 — dampens the
# contribution of low-ranked items so the top of each list dominates.
RRF_K = 60

# Pre-fetch ceiling per leg before fusion. Wider than the caller's k so
# that a chunk that's #40 in one list but #2 in the other still has a
# shot at the final top-K. 50 is the spec value.
HYBRID_FETCH_PER_LEG = 50

# Strips non-word characters except spaces; whitespace then collapses
# into FTS5 tokens. The result is OR-joined so MATCH is bag-of-words.
_FTS5_SAFE_RE = re.compile(r"[^\w\s]+", re.UNICODE)


def _hybrid_enabled() -> bool:
    """Read RAG_HYBRID_SEARCH live so tests / operators can flip it
    without re-importing the module. Truthy values: 1, true, yes, on
    (case-insensitive). Default: true."""
    raw = os.getenv("RAG_HYBRID_SEARCH", "true")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _sanitize_fts5_query(query: str) -> str:
    """Strip punctuation, collapse whitespace, and re-join tokens with
    ``OR`` so the resulting MATCH clause is bag-of-words rather than
    "every token must appear."

    AND semantics return zero hits on natural-language queries because
    BM25 can never contribute and hybrid collapses to semantic-only.
    OR is the standard bag-of-words relaxation BM25 expects.

    Empty → empty (caller treats that as "no BM25 leg")."""
    if not query:
        return ""
    cleaned = _FTS5_SAFE_RE.sub(" ", query)
    tokens = cleaned.split()
    if not tokens:
        return ""
    return " OR ".join(tokens)


# ── Public types ──────────────────────────────────────────────────────────


@dataclass
class Chunk:
    """One indexed chunk. ``embedding`` is omitted from the public
    serializer to keep response payloads small.

    ``rrf_score`` is set on results from the hybrid path (debug-only,
    not serialized). ``score`` remains the primary ranking signal —
    cosine for semantic-only, semantic cosine for hybrid results."""

    chunk_id: str
    project_id: str
    doc_id: str
    chunk_index: int
    text: str
    score: Optional[float] = None  # set on search results, None when raw
    rrf_score: Optional[float] = field(default=None, repr=False, compare=False)
    # Retrieval layer this chunk came from, set by ``retrieve_with_filter``:
    # "own" (the active project), "general_knowledge" (a disclosed curated GK
    # project), or "master_corpus" (the labeled empty/thin fallback corpus).
    # STEP 0 isolation signal — never sent to the LLM or the wire; the chat
    # runtime reads it to disclose a Master-Corpus fallback in the answer +
    # sources panel. compare=False so it never affects Chunk equality in tests.
    layer: str = field(default="own", compare=False)
    # Layered RAG (docs/rag-deployment-plan.md). ``knowledge_layer`` is the
    # PERSISTED L1/L2A/L2B/L3 layer (see app.core.rag.layers.LAYERS), hydrated
    # by ``search`` from the stored column. It is deliberately a SEPARATE field
    # from ``layer`` above: ``layer`` is the per-query STEP 0 isolation tag that
    # the retriever recomputes on every search, so it cannot also carry the
    # persisted layer. ``authority`` is the persisted authority label (one of
    # app.core.rag.layers.AUTHORITIES). Both drive the authority-precedence
    # re-rank when RAG_LAYERED is on, and both are None for unlayered rows.
    # compare=False so neither affects Chunk equality in tests.
    knowledge_layer: Optional[str] = field(default=None, compare=False)
    authority: Optional[str] = field(default=None, compare=False)
    # Revision currency (app.core.rag.revision, audit §5.2). Filename-derived,
    # set by ``retrieve_with_filter`` on every search: ``revision`` is the
    # parsed single-char revision token ("" when none), ``drawing_number`` the
    # parsed sheet code, ``superseded`` True when the filename marks the doc
    # obsolete. Internal ranking + answer-disclosure signals — never serialized
    # to the wire; compare=False so none affects Chunk equality in tests.
    revision: str = field(default="", compare=False)
    drawing_number: str = field(default="", compare=False)
    superseded: bool = field(default=False, compare=False)
    # ── photo_chunks fields (kind="photo") ─────────────────────────────
    # text chunks keep kind="text" and the photo fields default to None.
    kind: str = "text"
    sha256: Optional[str] = None
    photo_url: Optional[str] = None
    photo_metadata: Optional[dict] = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        # Drop None scores from API responses
        if d["score"] is None:
            d.pop("score")
        # rrf_score is debug-only; never expose it via the wire
        d.pop("rrf_score", None)
        # layer/knowledge_layer/authority are internal ranking+isolation
        # signals; keep them off the API payload
        d.pop("layer", None)
        d.pop("knowledge_layer", None)
        d.pop("authority", None)
        # Revision-currency signals are internal (ranking + disclosure), not wire
        d.pop("revision", None)
        d.pop("drawing_number", None)
        d.pop("superseded", None)
        # Drop photo fields for plain text chunks to keep payloads small
        if d.get("kind") == "text":
            d.pop("sha256", None)
            d.pop("photo_url", None)
            d.pop("photo_metadata", None)
        return d


# ── Module-level cache (one store per database URL) ───────────────────────

_STORE_CACHE: dict = {}
_CACHE_LOCK = Lock()
_INITIALIZED_NAMESPACES: Set[Tuple[str, str]] = set()
_INIT_LOCK = Lock()


def _rag_vector_namespace() -> str:
    """Active RAG vector namespace. Legacy ``chunks`` table = empty string."""
    return os.getenv("RAG_VECTOR_NAMESPACE", "v2").strip()


def get_store(
    dim: Optional[int] = None,
    db_path: Optional[str] = None,
    namespace: Optional[str] = None,
    model_name: Optional[str] = None,
) -> "VectorStore":
    """Process-cached store. Different ``db_path``/namespace values get
    different cached instances, which keeps tests isolated when they swap
    DATA_DIR or embedder.

    ``dim`` is optional; when omitted the store uses the configured
    embedder's actual dimension so the table width always matches the
    vectors being written.
    """
    path = db_path or _default_db_path()
    url = _database_url(path)
    ns = namespace if namespace is not None else _rag_vector_namespace()
    embedder = get_embedder(model_name=model_name)
    identity = embedder.identity
    actual_dim = dim if dim is not None else identity["dim"]
    key = (url, ns, identity["model"], identity["dim"])
    with _CACHE_LOCK:
        if key not in _STORE_CACHE:
            _STORE_CACHE[key] = VectorStore(
                db_path=path,
                dim=actual_dim,
                namespace=ns,
                model_name=identity["model"],
            )
    return _STORE_CACHE[key]


def reset_store_cache() -> None:
    """Drop all cached stores. Used by tests to pick up a swapped DATA_DIR."""
    global _STORE_CACHE, _INITIALIZED_NAMESPACES
    with _CACHE_LOCK:
        for s in _STORE_CACHE.values():
            try:
                s.close()
            except Exception:
                logger.warning(
                    "swallowed %s in reset_store_cache() — continuing",
                    "Exception", exc_info=True,
                )
        _STORE_CACHE = {}
    with _INIT_LOCK:
        _INITIALIZED_NAMESPACES = set()


def _default_db_path() -> str:
    """Default backing path/URL for the unified schema database."""
    url = get_database_url()
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///") :]
    return url


def _database_url(db_path: str) -> str:
    """Map a legacy file path or explicit URL to a SQLAlchemy database URL."""
    if "://" in db_path:
        return db_path
    return f"sqlite:///{os.path.abspath(db_path)}"


def _ensure_sqlite_parent_dir(url: str) -> None:
    if url.startswith("sqlite:///"):
        parent = os.path.dirname(url[len("sqlite:///") :])
        if parent:
            os.makedirs(parent, exist_ok=True)


def _ensure_hnsw_index(eng, table_name: str) -> None:
    """Ensure the pgvector HNSW ANN index on ``{table}.embedding`` exists.

    Semantic search runs ``ORDER BY embedding <=> q``; WITHOUT this index that
    is an exact sequential scan — fine at ~10k chunks, catastrophic at 100k+
    (the drive_archive GK layer is 133k: per-query full-scan hit tens of
    seconds and dropped the DB). HNSW + ``vector_cosine_ops`` matches the
    ``<=>`` cosine operator the retriever uses. ``IF NOT EXISTS`` makes this a
    one-time build; later boots skip it. Never raises — a missing index
    degrades to full-scan, it must not crash startup.
    """
    try:
        with eng.begin() as conn:
            conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS {table_name}_embedding_hnsw "
                f"ON {table_name} USING hnsw (embedding vector_cosine_ops)"
            ))
    except Exception:  # noqa: BLE001 — full-scan fallback, never block startup
        logger.warning(
            "could not ensure hnsw index on %s (semantic search will "
            "seq-scan until it exists)", table_name, exc_info=True,
        )


def _ensure_schema(url: str, rag_chunk_cls: type) -> None:
    global _INITIALIZED_NAMESPACES
    table_name = rag_chunk_cls.__tablename__
    init_key = (url, table_name)
    if init_key in _INITIALIZED_NAMESPACES:
        return
    with _INIT_LOCK:
        if init_key in _INITIALIZED_NAMESPACES:
            return
        _ensure_sqlite_parent_dir(url)
        eng = _engine_for_url(url)
        rag_chunk_cls.__table__.create(bind=eng, checkfirst=True)
        # `checkfirst=True` above SKIPS the whole table create — indexes
        # included — when the table already exists. A prod table that predates
        # the idx_chunks_project declaration therefore never got the btree, so
        # COUNT/filter-by-project seq-scans the full table (~11s on the master
        # corpus; pgvector search stays fast via its own index). Create each
        # declared index explicitly + idempotently so legacy tables get it too.
        for index in rag_chunk_cls.__table__.indexes:
            try:
                index.create(bind=eng, checkfirst=True)
            except Exception:  # noqa: BLE001 — never block startup on an index
                # Not blocking startup is right; staying SILENT is not. The
                # comment above measures the cost of a missing btree here:
                # ~11s seq-scans on the master corpus. Swallowed, that shows
                # up as "retrieval got slow" with nothing to point at.
                logger.warning(
                    "could not create index %s on %s — queries filtering by "
                    "project_id may fall back to a sequential scan",
                    getattr(index, "name", "?"), rag_chunk_cls.__tablename__,
                    exc_info=True,
                )
        # PostgreSQL BM25 leg: the ``text_search`` GENERATED column + GIN
        # index. Alembic 0003 only covers the legacy ``chunks`` table;
        # namespaced tables (chunks_v2, ...) are created HERE, so they must
        # get the column here too or ``_bm25_postgres`` fails with
        # UndefinedColumn. Idempotent via IF NOT EXISTS.
        if eng.dialect.name == "postgresql":
            try:
                with eng.begin() as conn:
                    conn.execute(text(
                        f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS "
                        "text_search tsvector GENERATED ALWAYS AS "
                        "(to_tsvector('english', text)) STORED"
                    ))
                    conn.execute(text(
                        f"CREATE INDEX IF NOT EXISTS {table_name}_fts_gin "
                        f"ON {table_name} USING GIN (text_search)"
                    ))
            except Exception:  # noqa: BLE001 — BM25 degrades, never block startup
                logger.warning(
                    "could not ensure text_search column on %s", table_name,
                    exc_info=True,
                )
            _ensure_hnsw_index(eng, table_name)
        _INITIALIZED_NAMESPACES.add(init_key)


# ── Store ────────────────────────────────────────────────────────────────


class VectorStore:
    """SQLAlchemy-backed chunk store with pgvector search on PostgreSQL.

    Thread-safety: one session per operation, guarded by an internal lock.
    Sufficient for the chat path (one query per request); not designed for
    massive parallel ingest.
    """

    def __init__(
        self,
        db_path: str,
        dim: int = EMBEDDING_DIM,
        namespace: Optional[str] = None,
        model_name: Optional[str] = None,
    ):
        self.db_path = db_path
        self.dim = dim
        self.namespace = namespace if namespace is not None else _rag_vector_namespace()
        self.model_name = model_name or os.getenv("RAG_EMBEDDING_MODEL") or "fake"
        self._lock = Lock()
        self._database_url = _database_url(db_path)
        self._use_pgvector = self._database_url.startswith("postgresql")
        # Build the namespaced model class. The old ``chunks`` table is
        # retired in place: namespace="" maps to it, but prod defaults to
        # namespace="v2" so new writes never touch the contaminated table.
        self._rag_chunk_cls = make_rag_chunk_class(
            namespace=self.namespace,
            dim=dim,
            model_name=self.model_name,
        )
        self._table_name = rag_chunk_table_name(self.namespace)
        _ensure_schema(self._database_url, self._rag_chunk_cls)
        # Fail loud if the namespace already contains vectors from a
        # different embedder. Mixed-model contamination must be impossible.
        self._verify_embedding_identity()
        # FTS5 mirror (SQLite only). Idempotent.
        if not self._use_pgvector:
            self._ensure_fts5_sqlite()

    @property
    def fast_search(self) -> bool:
        """True when search uses pgvector ANN on PostgreSQL."""
        return self._use_pgvector

    def _verify_embedding_identity(self) -> None:
        """Fail loud if the namespace contains chunks from a different model.

        Checks a representative row. If any row has a different model/dim/
        normalized flag, the store refuses to operate. This is the structural
        guard that makes mixed-model contamination impossible.

        Skipped for the legacy namespace because the original ``chunks`` table
        predates the identity columns; it is retired in place and never used
        for new embeddings.
        """
        cls = self._rag_chunk_cls
        # Legacy table has no identity columns — nothing to verify.
        if not hasattr(cls, "embedding_model"):
            return
        expected = {
            "model": self.model_name,
            "dim": self.dim,
            "normalized": True,
        }
        with self._lock:
            with self._session_factory()() as session:
                row = session.execute(
                    select(
                        cls.embedding_model,
                        cls.embedding_dim,
                        cls.embedding_normalized,
                    ).limit(1)
                ).first()
        if row is None:
            return
        stored = {
            "model": row.embedding_model,
            "dim": row.embedding_dim,
            "normalized": row.embedding_normalized,
        }
        if stored != expected:
            raise RuntimeError(
                f"Embedding identity mismatch in namespace {self.namespace!r}: "
                f"expected {expected}, found {stored}. "
                f"Mixed-model namespaces are not allowed."
            )

    def close(self) -> None:
        pass

    def _session_factory(self):
        return _session_factory_for_url(self._database_url)

    def _ensure_fk_parents(self, session: Session, project_id: str, doc_id: str) -> None:
        """Satisfy chunks FK on PostgreSQL when tests use bare project/doc ids."""
        if not self._use_pgvector:
            return
        if session.get(Project, project_id) is None:
            session.add(
                Project(
                    id=project_id,
                    name=project_id,
                    client=None,
                    status="active",
                    aconex_connected=False,
                    user_id="system",
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
            )
            session.flush()
        if session.get(Document, doc_id) is None:
            session.add(
                Document(
                    id=doc_id,
                    project_id=project_id,
                    original_name=doc_id,
                    stored_as=None,
                    file_path=None,
                    doc_type="document",
                    doc_role="other",
                    size=0,
                    uploaded_at=datetime.now(timezone.utc).isoformat(),
                    content_sha256=None,
                )
            )
            session.flush()

    # ── FTS5 mirror (SQLite path) ────────────────────────────────────────

    def _ensure_fts5_sqlite(self) -> None:
        """Create the FTS5 mirror + AI/AD/AU sync triggers on first init,
        then backfill any rows that pre-date the FTS5 table.

        Schema: external-content (``content='<table>'``,
        ``content_rowid='rowid'``). Text isn't duplicated; FTS5 reads it
        from the source table at query time via ``rowid``. Joining back to
        the source table by rowid restores the chunk tuple bm25_search
        returns.

        Idempotent: presence-check short-circuits subsequent calls.
        """
        table = self._table_name
        fts_table = f"{table}_fts"
        with self._lock:
            with self._session_factory()() as session:
                conn = session.connection()
                row = conn.exec_driver_sql(
                    "SELECT sql FROM sqlite_master "
                    f"WHERE type='table' AND name='{fts_table}'"
                ).fetchone()
                if row is not None:
                    # Already exists. Verify it's the external-content
                    # shape we expect; if a future change introduces a
                    # different shape, surface it so we don't silently
                    # bm25 against the wrong schema.
                    existing_sql = (row[0] or "").lower()
                    if f"content='{table}'" not in existing_sql and f'content="{table}"' not in existing_sql:
                        logger.warning(
                            "%s exists but is not external-content; "
                            "BM25 results may be unreliable. sql=%r",
                            fts_table, row[0],
                        )
                    return
                conn.exec_driver_sql(
                    f"CREATE VIRTUAL TABLE {fts_table} USING fts5("
                    f"text, content='{table}', content_rowid='rowid')"
                )
                conn.exec_driver_sql(
                    f"CREATE TRIGGER {table}_ai AFTER INSERT ON {table} BEGIN "
                    f"INSERT INTO {fts_table}(rowid, text) VALUES (new.rowid, new.text); "
                    "END"
                )
                conn.exec_driver_sql(
                    f"CREATE TRIGGER {table}_ad AFTER DELETE ON {table} BEGIN "
                    f"INSERT INTO {fts_table}({fts_table}, rowid, text) "
                    "VALUES('delete', old.rowid, old.text); "
                    "END"
                )
                conn.exec_driver_sql(
                    f"CREATE TRIGGER {table}_au AFTER UPDATE ON {table} BEGIN "
                    f"INSERT INTO {fts_table}({fts_table}, rowid, text) "
                    "VALUES('delete', old.rowid, old.text); "
                    f"INSERT INTO {fts_table}(rowid, text) VALUES (new.rowid, new.text); "
                    "END"
                )
                # One-time backfill — bulk insert via SELECT is fast
                # (a few seconds even for 140k rows on local SSD).
                cur = conn.exec_driver_sql(
                    f"INSERT INTO {fts_table}(rowid, text) "
                    f"SELECT rowid, text FROM {table}"
                )
                n = cur.rowcount if cur.rowcount is not None else 0
                session.commit()
                # One-line operator log on first init.
                print(f"vector_store: FTS5 backfilled {n} rows", flush=True)

    # ── Writes ───────────────────────────────────────────────────────────

    def upsert_chunks(
        self,
        project_id: str,
        doc_id: str,
        chunks: List[str],
        embeddings: np.ndarray,
        *,
        knowledge_layer: Optional[str] = None,
        authority: Optional[str] = None,
    ) -> int:
        """Replace all existing chunks for ``(project_id, doc_id)`` with
        the supplied set. Idempotent — calling twice with the same input
        is a no-op net change.

        ``embeddings`` must be a 2-D array of shape ``(len(chunks), dim)``.
        Returns the number of chunks written.

        FTS5 sync (SQLite path): the AI/AD/AU triggers on ``chunks``
        keep ``chunks_fts`` in lock-step automatically. PostgreSQL path:
        the ``text_search`` tsvector column is GENERATED ALWAYS — the
        DB recomputes it on write, no application-side work.
        """
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"chunks/embeddings length mismatch: {len(chunks)} vs {len(embeddings)}"
            )
        if len(chunks) == 0:
            self.delete_doc(project_id, doc_id)
            return 0

        emb = np.asarray(embeddings, dtype=np.float32)
        if emb.shape[1] != self.dim:
            raise ValueError(
                f"embedding dim {emb.shape[1]} != store dim {self.dim}"
            )

        now = _now()
        with self._lock:
            with self._session_factory()() as session:
                self._ensure_fk_parents(session, project_id, doc_id)
                session.execute(
                    delete(self._rag_chunk_cls).where(
                        self._rag_chunk_cls.project_id == project_id,
                        self._rag_chunk_cls.doc_id == doc_id,
                    )
                )
                for i, (txt, vec) in enumerate(zip(chunks, emb)):
                    if "\x00" in txt:
                        # PostgreSQL rejects NUL bytes in text columns with
                        # DataError, which would abort this whole document's
                        # insert. Extraction already strips NUL; this is the
                        # last line of defence for any other write path.
                        txt = txt.replace("\x00", "")
                    session.add(
                        self._rag_chunk_cls(
                            chunk_id=f"{project_id}:{doc_id}:{i}",
                            project_id=project_id,
                            doc_id=doc_id,
                            chunk_index=i,
                            text=txt,
                            embedding=vec,
                            embedding_model=self.model_name,
                            embedding_dim=self.dim,
                            embedding_normalized=True,
                            created_at=now,
                            knowledge_layer=knowledge_layer,
                            authority=authority,
                        )
                    )
                session.commit()
        return len(chunks)

    def delete_doc(self, project_id: str, doc_id: str) -> int:
        with self._lock:
            with self._session_factory()() as session:
                result = session.execute(
                    delete(self._rag_chunk_cls).where(
                        self._rag_chunk_cls.project_id == project_id,
                        self._rag_chunk_cls.doc_id == doc_id,
                    )
                )
                session.commit()
                return int(result.rowcount or 0)

    # ── Read ─────────────────────────────────────────────────────────────

    def count(self, project_id: Optional[str] = None) -> int:
        with self._lock:
            with self._session_factory()() as session:
                stmt = select(func.count()).select_from(self._rag_chunk_cls)
                if project_id is not None:
                    stmt = stmt.where(self._rag_chunk_cls.project_id == project_id)
                return int(session.scalar(stmt) or 0)

    def count_by_doc(self, project_id: str) -> Dict[str, int]:
        """Return a ``{doc_id: chunk_count}`` map for ``project_id``.

        Uses the ``idx_chunks_doc`` index; one small GROUP BY regardless of
        corpus size. This is the source of truth for per-document chunk
        counts, including chunks written by ``/v1/admin/corpus/bulk-insert``
        (which bypasses the legacy ``doc_index`` JSON blob).
        """
        with self._lock:
            with self._session_factory()() as session:
                stmt = (
                    select(self._rag_chunk_cls.doc_id, func.count())
                    .where(self._rag_chunk_cls.project_id == project_id)
                    .group_by(self._rag_chunk_cls.doc_id)
                )
                return {row[0]: int(row[1]) for row in session.execute(stmt).all()}

    def doc_chunk_texts(
        self, project_id: str, doc_ids: List[str]
    ) -> Dict[str, List[str]]:
        """Return ``{doc_id: [chunk_text, ...]}`` (ordered by chunk_index) for
        the given docs in ``project_id``.

        Used by the corpus delete-docs admin endpoint to export a restore
        bundle *before* deleting — so the exact text leaving the RAG is
        captured. Bounded by the caller's doc-id list, not the corpus size.
        """
        if not doc_ids:
            return {}
        out: Dict[str, List[str]] = {d: [] for d in doc_ids}
        with self._lock:
            with self._session_factory()() as session:
                stmt = (
                    select(
                        self._rag_chunk_cls.doc_id,
                        self._rag_chunk_cls.chunk_index,
                        self._rag_chunk_cls.text,
                    )
                    .where(
                        self._rag_chunk_cls.project_id == project_id,
                        self._rag_chunk_cls.doc_id.in_(doc_ids),
                    )
                    .order_by(
                        self._rag_chunk_cls.doc_id,
                        self._rag_chunk_cls.chunk_index,
                    )
                )
                for did, _idx, txt in session.execute(stmt).all():
                    out.setdefault(did, []).append(txt or "")
        return out

    def search(
        self,
        project_id: str,
        query_vec: np.ndarray,
        k: int = 5,
        query_text: Optional[str] = None,
    ) -> List[Chunk]:
        """Top-``k`` chunks for ``project_id``.

        Two modes:

        - **Semantic-only** (legacy): when ``RAG_HYBRID_SEARCH`` is
          falsy, ``query_text`` is None/empty, or no BM25 hits come
          back. Ranks by cosine similarity. Byte-for-byte the
          pre-hybrid behavior — existing callers untouched.

        - **Hybrid** (default when env flag is truthy AND ``query_text``
          is provided): pulls 50 semantic + 50 BM25 candidates, fuses
          with Reciprocal Rank Fusion (k=60), returns top ``k`` by RRF
          score. Each returned chunk keeps its semantic cosine in
          ``.score`` and gets ``.rrf_score`` set for debug.

        Returns an empty list when the project has no indexed chunks.

        The legacy positional signature ``search(project_id, query_vec,
        k=...)`` is preserved — ``query_text`` is a keyword-only-style
        opt-in. ``retriever.py`` does NOT pass it today, so the chat
        path stays on semantic-only until the operator wires it.
        """
        q = np.asarray(query_vec, dtype=np.float32)
        if q.ndim != 1 or q.shape[0] != self.dim:
            raise ValueError(
                f"query_vec must be 1-D of length {self.dim}; got shape {q.shape}"
            )

        hybrid = (
            _hybrid_enabled()
            and query_text is not None
            and query_text.strip() != ""
        )

        if not hybrid:
            return self._semantic_search(project_id, q, k)

        sem_results = self._semantic_search(project_id, q, HYBRID_FETCH_PER_LEG)
        bm25_results = self.bm25_search(project_id, query_text, HYBRID_FETCH_PER_LEG)

        if not bm25_results:
            # Graceful degrade to semantic-only; respect caller's k.
            return sem_results[:k]

        return _rrf_combine(sem_results, bm25_results, k)

    def identifier_search(
        self,
        project_id: str,
        identifiers: List[str],
        k: int = 20,
    ) -> List[Chunk]:
        """Lexical exact-match search for construction reference identifiers.

        Searches the raw chunk text for any of the supplied identifier
        strings using case-insensitive token matching.  Intervening
        punctuation/label words (e.g. "VO Ref: 99" vs "VO 99") are ignored.
        No reindexing is required: this works against the existing
        ``chunks.text`` column.

        Scoring: each returned chunk gets a score equal to the fraction of
        identifiers that appear in its text. This lets callers boost chunks
        that match multiple reference tokens (e.g. a VO number + a date)
        above chunks that only match one generic token.

        Returns an empty list when ``identifiers`` is empty or no chunk
        contains any of them.
        """
        if not identifiers or not project_id:
            return []

        # Tokenise each identifier so punctuation between tokens is ignored.
        ident_tokens: List[List[str]] = []
        for ident in identifiers:
            tokens = [t for t in re.split(r"[^a-z0-9]+", ident.lower()) if t]
            if tokens:
                ident_tokens.append(tokens)
        if not ident_tokens:
            return []

        # Build a pre-filter: every token of an identifier must appear as a
        # substring in LOWER(text).  Tokens are alphanumeric so no LIKE
        # wildcard escaping is required.
        ident_clauses: List[str] = []
        params: Dict[str, Any] = {"project_id": project_id, "k": k}
        param_idx = 0
        for tokens in ident_tokens:
            token_clauses: List[str] = []
            for tok in tokens:
                token_clauses.append(f"LOWER(text) LIKE :p{param_idx}")
                params[f"p{param_idx}"] = f"%{tok}%"
                param_idx += 1
            ident_clauses.append("(" + " AND ".join(token_clauses) + ")")

        like_clauses = " OR ".join(ident_clauses)
        sql = text(
            "SELECT chunk_id, project_id, doc_id, chunk_index, text, "
            "knowledge_layer, authority "
            f"FROM {self._table_name} "
            "WHERE project_id = :project_id "
            f"AND ({like_clauses}) "
            "LIMIT :k"
        )

        try:
            with self._lock:
                with self._session_factory()() as session:
                    rows = session.execute(sql, params).all()
        except OperationalError as e:
            logger.warning(
                "identifier_search failed for project=%s: %s; identifiers=%r",
                project_id, e, identifiers,
            )
            return []

        def _tokens(text: str) -> Set[str]:
            return set(re.split(r"[^a-z0-9]+", (text or "").lower())) - {""}

        text_tokens_by_row: Dict[Any, Set[str]] = {}
        out: List[Chunk] = []
        for r in rows:
            if r not in text_tokens_by_row:
                text_tokens_by_row[r] = _tokens(r.text)
            row_tokens = text_tokens_by_row[r]
            matches = sum(
                1 for tokens in ident_tokens if tokens and all(t in row_tokens for t in tokens)
            )
            score = matches / len(ident_tokens) if ident_tokens else 0.0
            out.append(
                Chunk(
                    chunk_id=r.chunk_id,
                    project_id=r.project_id,
                    doc_id=r.doc_id,
                    chunk_index=int(r.chunk_index),
                    text=r.text,
                    score=round(score, 4),
                    knowledge_layer=r.knowledge_layer,
                    authority=r.authority,
                )
            )
        # Discard SQL pre-filter false positives: a chunk that passed the
        # LIKE clauses but has no identifier token in its word-set gets
        # score 0.0 and must not be returned.
        out = [c for c in out if c.score > 0]
        # Higher match fraction first; preserve stable order on ties.
        out.sort(key=lambda c: -c.score)
        return out

    def _semantic_search(
        self,
        project_id: str,
        q: np.ndarray,
        k: int,
    ) -> List[Chunk]:
        """Semantic leg — dispatches on backend. Pulled out of search()
        so the hybrid path can reuse without re-validating the query."""
        if self._use_pgvector:
            return self._search_pgvector(project_id, q, k)
        return self._search_numpy(project_id, q, k)

    def _search_pgvector(
        self, project_id: str, query_vec: np.ndarray, k: int
    ) -> List[Chunk]:
        q_list = query_vec.tolist()
        # EmbeddingVector is a TypeDecorator; cast to Vector for pgvector ops.
        vec_col = cast(self._rag_chunk_cls.embedding, Vector(self.dim))
        distance = vec_col.cosine_distance(q_list)
        score_expr = (1 - distance).label("score")
        stmt = (
            select(
                self._rag_chunk_cls.chunk_id,
                self._rag_chunk_cls.project_id,
                self._rag_chunk_cls.doc_id,
                self._rag_chunk_cls.chunk_index,
                self._rag_chunk_cls.text,
                self._rag_chunk_cls.knowledge_layer,
                self._rag_chunk_cls.authority,
                score_expr,
            )
            .where(self._rag_chunk_cls.project_id == project_id)
            .order_by(distance)
            .limit(k)
        )
        with self._lock:
            with self._session_factory()() as session:
                rows = session.execute(stmt).all()
        return [
            Chunk(
                chunk_id=row.chunk_id,
                project_id=row.project_id,
                doc_id=row.doc_id,
                chunk_index=int(row.chunk_index),
                text=row.text,
                score=float(row.score),
                knowledge_layer=row.knowledge_layer,
                authority=row.authority,
            )
            for row in rows
        ]

    def _search_numpy(
        self, project_id: str, query_vec: np.ndarray, k: int
    ) -> List[Chunk]:
        stmt = select(self._rag_chunk_cls).where(self._rag_chunk_cls.project_id == project_id)
        with self._lock:
            with self._session_factory()() as session:
                rows = session.scalars(stmt).all()

        if not rows:
            return []

        embs = np.stack([np.asarray(r.embedding, dtype=np.float32) for r in rows])
        sims = embs @ query_vec
        order = np.argsort(-sims)[:k]
        out: List[Chunk] = []
        for idx in order:
            r = rows[int(idx)]
            out.append(
                Chunk(
                    chunk_id=r.chunk_id,
                    project_id=r.project_id,
                    doc_id=r.doc_id,
                    chunk_index=int(r.chunk_index),
                    text=r.text,
                    score=float(sims[int(idx)]),
                    knowledge_layer=getattr(r, "knowledge_layer", None),
                    authority=getattr(r, "authority", None),
                )
            )
        return out

    # ── BM25 leg ─────────────────────────────────────────────────────────

    def bm25_search(
        self,
        project_id: str,
        query: str,
        k: int = 50,
    ) -> List[Chunk]:
        """Top-``k`` chunks for ``project_id`` ranked by BM25.

        Dispatches on backend:

        - PostgreSQL: ``ts_rank`` over ``text_search`` (the tsvector
          column added by Alembic 0003) with a ``@@ plainto_tsquery``
          predicate. GIN index ``chunks_fts_gin`` keeps it fast.
        - SQLite: FTS5 ``chunks_fts`` external-content virtual table,
          joined back to ``chunks`` by rowid.

        Empty query (after sanitization) → empty list. Malformed
        backend errors → empty list (logged); caller treats as
        "semantic only" and continues.

        Returned chunks carry ``score`` set to the BM25 rank value for
        debug; the RRF fuser uses positional rank, not ``.score``.
        """
        if not query or not query.strip():
            return []

        if self._use_pgvector:
            return self._bm25_postgres(project_id, query, k)
        return self._bm25_sqlite(project_id, query, k)

    def _bm25_postgres(
        self, project_id: str, query: str, k: int
    ) -> List[Chunk]:
        """ts_rank + GIN. The plainto_tsquery accepts natural language
        (no manual sanitization needed; Postgres handles it).
        """
        table = self._table_name
        sql = text(
            f"""
            SELECT c.chunk_id, c.project_id, c.doc_id, c.chunk_index,
                   c.text, c.knowledge_layer, c.authority,
                   ts_rank(c.text_search, q) AS rank
            FROM {table} c, plainto_tsquery('english', :q) AS q
            WHERE c.text_search @@ q
              AND c.project_id = :project_id
            ORDER BY rank DESC
            LIMIT :k
            """
        )
        try:
            with self._lock:
                with self._session_factory()() as session:
                    rows = session.execute(
                        sql,
                        {"q": query, "project_id": project_id, "k": k},
                    ).all()
        except OperationalError as e:
            logger.warning(
                "bm25_search (postgres) failed: %s; query=%r", e, query
            )
            return []
        return [
            Chunk(
                chunk_id=r.chunk_id,
                project_id=r.project_id,
                doc_id=r.doc_id,
                chunk_index=int(r.chunk_index),
                text=r.text,
                score=float(r.rank),
                knowledge_layer=r.knowledge_layer,
                authority=r.authority,
            )
            for r in rows
        ]

    def _bm25_sqlite(
        self, project_id: str, query: str, k: int
    ) -> List[Chunk]:
        """FTS5 MATCH joined to chunks by rowid (external-content shape).
        FTS5's ``rank`` is a negated BM25 — lower = better — so ASC."""
        safe_query = _sanitize_fts5_query(query)
        if not safe_query:
            return []
        table = self._table_name
        fts_table = f"{table}_fts"
        sql = text(
            f"""
            SELECT c.chunk_id, c.project_id, c.doc_id, c.chunk_index,
                   c.text, c.knowledge_layer, c.authority,
                   {fts_table}.rank AS bm25_rank
            FROM {fts_table}
            JOIN {table} c ON c.rowid = {fts_table}.rowid
            WHERE {fts_table} MATCH :q
              AND c.project_id = :project_id
            ORDER BY {fts_table}.rank
            LIMIT :k
            """
        )
        try:
            with self._lock:
                with self._session_factory()() as session:
                    rows = session.execute(
                        sql,
                        {"q": safe_query, "project_id": project_id, "k": k},
                    ).all()
        except OperationalError as e:
            # FTS5 raises on malformed MATCH input. Treat as "no matches."
            logger.warning(
                "bm25_search (sqlite) FTS5 MATCH failed: %s; query=%r",
                e,
                safe_query,
            )
            return []
        return [
            Chunk(
                chunk_id=r.chunk_id,
                project_id=r.project_id,
                doc_id=r.doc_id,
                chunk_index=int(r.chunk_index),
                text=r.text,
                score=float(r.bm25_rank),
                knowledge_layer=r.knowledge_layer,
                authority=r.authority,
            )
            for r in rows
        ]

    def bm25_search_photos(
        self,
        query: str,
        k: int = 5,
        project_id: Optional[str] = None,
    ) -> List[Chunk]:
        """Search the ``photo_chunks`` table by caption keywords.

        Returns photo chunks ordered by caption relevance. When ``project_id``
        is supplied, global (NULL-project) photos are included plus photos
        owned by that project; other projects' photos are excluded. If the
        table does not exist (older deployments) an empty list is returned.
        """
        if not query or not query.strip():
            return []

        # Tokenise into SQL LIKE clauses (OR). Keeps the method usable on
        # a plain SQLite table without requiring an FTS5 mirror.
        tokens = [t for t in _FTS5_SAFE_RE.sub(" ", query).strip().split() if t]
        if not tokens:
            return []

        project_filter = ""
        params: Dict[str, Any] = {"k": k}
        if project_id is not None:
            project_filter = " AND (project_id IS NULL OR project_id = :project_id)"
            params["project_id"] = project_id

        like_clauses = " OR ".join(
            f"caption LIKE :like_{i}" for i in range(len(tokens))
        )
        for i, tok in enumerate(tokens):
            params[f"like_{i}"] = f"%{tok}%"

        sql = text(
            f"SELECT chunk_id, project_id, sha256, caption, photo_metadata "
            f"FROM photo_chunks WHERE ({like_clauses}){project_filter} "
            f"ORDER BY created_at DESC LIMIT :k"
        )

        try:
            with self._lock:
                with self._session_factory()() as session:
                    rows = session.execute(sql, params).all()
        except OperationalError:
            # photo_chunks table may not exist on older deployments.
            return []

        out: List[Chunk] = []
        for r in rows:
            meta = None
            photo_url = None
            if r.photo_metadata:
                try:
                    meta = json_lib.loads(r.photo_metadata)
                    photo_url = meta.get("source_url") if isinstance(meta, dict) else None
                except Exception:
                    logger.warning(
                        "swallowed %s in bm25_search_photos() — continuing",
                        "Exception", exc_info=True,
                    )
            out.append(
                Chunk(
                    chunk_id=r.chunk_id,
                    project_id=r.project_id or "",
                    doc_id=r.chunk_id,
                    chunk_index=0,
                    text=r.caption,
                    score=1.0,
                    kind="photo",
                    sha256=r.sha256,
                    photo_url=photo_url,
                    photo_metadata=meta,
                )
            )
        return out

# ── Helpers ───────────────────────────────────────────────────────────────


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rrf_combine(
    semantic: List[Chunk],
    bm25: List[Chunk],
    top_k: int,
) -> List[Chunk]:
    """Reciprocal Rank Fusion (Cormack et al. 2009).

    Combines two ranked lists by summing 1/(RRF_K + rank) contributions.
    Chunks present in only one list still get a score from that list
    (the other term is 0). When a chunk appears in both lists the
    semantic instance is kept (its ``.score`` cosine attaches), so
    callers reading ``.score`` see semantic relevance. ``.rrf_score``
    is set for debug. Position in the returned list = RRF score desc.
    """
    sem_rank = {c.chunk_id: r for r, c in enumerate(semantic, 1)}
    bm_rank = {c.chunk_id: r for r, c in enumerate(bm25, 1)}
    # Semantic instances take precedence on tie (they carry .score).
    by_id: dict[str, Chunk] = {}
    for c in bm25:
        by_id[c.chunk_id] = c
    for c in semantic:
        by_id[c.chunk_id] = c
    scored: List[Tuple[float, Chunk]] = []
    for chunk_id, c in by_id.items():
        s = sem_rank.get(chunk_id)
        b = bm_rank.get(chunk_id)
        rrf = (
            (1.0 / (RRF_K + s) if s is not None else 0.0)
            + (1.0 / (RRF_K + b) if b is not None else 0.0)
        )
        try:
            c.rrf_score = rrf
        except Exception:
            logger.warning(
                "swallowed %s in _rrf_combine() — continuing",
                "Exception", exc_info=True,
            )
        scored.append((rrf, c))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:top_k]]
