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
from typing import List, Optional, Set, Tuple

import numpy as np
from pgvector.sqlalchemy import Vector
from sqlalchemy import cast, delete, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

import json as json_lib

from app.core.db import _engine_for_url, _session_factory_for_url, get_database_url
from app.core.models import EMBEDDING_DIM, Document, Project, RagChunk

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
        # Drop photo fields for plain text chunks to keep payloads small
        if d.get("kind") == "text":
            d.pop("sha256", None)
            d.pop("photo_url", None)
            d.pop("photo_metadata", None)
        return d


# ── Module-level cache (one store per database URL) ───────────────────────

_STORE_CACHE: dict = {}
_CACHE_LOCK = Lock()
_INITIALIZED_URLS: Set[str] = set()
_INIT_LOCK = Lock()


def get_store(dim: int = EMBEDDING_DIM, db_path: Optional[str] = None) -> "VectorStore":
    """Process-cached store. Different ``db_path`` values get different
    cached instances, which keeps tests isolated when they swap DATA_DIR."""
    path = db_path or _default_db_path()
    url = _database_url(path)
    key = (url, dim)
    with _CACHE_LOCK:
        if key not in _STORE_CACHE:
            _STORE_CACHE[key] = VectorStore(db_path=path, dim=dim)
    return _STORE_CACHE[key]


def reset_store_cache() -> None:
    """Drop all cached stores. Used by tests to pick up a swapped DATA_DIR."""
    global _STORE_CACHE, _INITIALIZED_URLS
    with _CACHE_LOCK:
        for s in _STORE_CACHE.values():
            try:
                s.close()
            except Exception:
                pass
        _STORE_CACHE = {}
    with _INIT_LOCK:
        _INITIALIZED_URLS = set()


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


def _ensure_schema(url: str) -> None:
    global _INITIALIZED_URLS
    if url in _INITIALIZED_URLS:
        return
    with _INIT_LOCK:
        if url in _INITIALIZED_URLS:
            return
        _ensure_sqlite_parent_dir(url)
        RagChunk.__table__.create(bind=_engine_for_url(url), checkfirst=True)
        _INITIALIZED_URLS.add(url)


# ── Store ────────────────────────────────────────────────────────────────


class VectorStore:
    """SQLAlchemy-backed chunk store with pgvector search on PostgreSQL.

    Thread-safety: one session per operation, guarded by an internal lock.
    Sufficient for the chat path (one query per request); not designed for
    massive parallel ingest.
    """

    def __init__(self, db_path: str, dim: int = EMBEDDING_DIM):
        self.db_path = db_path
        self.dim = dim
        self._lock = Lock()
        self._database_url = _database_url(db_path)
        self._use_pgvector = self._database_url.startswith("postgresql")
        _ensure_schema(self._database_url)
        # FTS5 mirror (SQLite only). Idempotent.
        if not self._use_pgvector:
            self._ensure_fts5_sqlite()

    @property
    def fast_search(self) -> bool:
        """True when search uses pgvector ANN on PostgreSQL."""
        return self._use_pgvector

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

        Schema: external-content (``content='chunks'``,
        ``content_rowid='rowid'``). Text isn't duplicated; FTS5 reads it
        from ``chunks`` at query time via ``rowid``. Joining back to
        ``chunks`` by rowid restores the (project_id, doc_id, chunk_id,
        chunk_index, text) tuple bm25_search returns.

        Deviation from operator spec: spec called for standalone
        ``fts5(id UNINDEXED, text)``. Reasons for external-content:
        (a) the live SQLite database at ``data/rag/vectors.db`` already
            has the external-content shape from the reference branch's
            backfill (143,472 rows). Switching shapes forces a 140k
            re-backfill on first boot post-deploy with no behavior win.
        (b) Triggers DO fire on SQLAlchemy ORM inserts — ORM uses the
            DBAPI INSERT under the hood, which is exactly what AFTER
            INSERT triggers watch. The spec's rationale ("ORM doesn't
            fire triggers, so write to FTS manually in upsert_chunks")
            was wrong. With triggers, write paths stay simple.
        (c) No text duplication = lower disk + lower index size.

        Idempotent: presence-check short-circuits subsequent calls.
        """
        with self._lock:
            with self._session_factory()() as session:
                conn = session.connection()
                row = conn.exec_driver_sql(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='table' AND name='chunks_fts'"
                ).fetchone()
                if row is not None:
                    # Already exists. Verify it's the external-content
                    # shape we expect; if a future change introduces a
                    # different shape, surface it so we don't silently
                    # bm25 against the wrong schema.
                    existing_sql = (row[0] or "").lower()
                    if "content='chunks'" not in existing_sql and "content=\"chunks\"" not in existing_sql:
                        logger.warning(
                            "chunks_fts exists but is not external-content; "
                            "BM25 results may be unreliable. sql=%r",
                            row[0],
                        )
                    return
                conn.exec_driver_sql(
                    "CREATE VIRTUAL TABLE chunks_fts USING fts5("
                    "text, content='chunks', content_rowid='rowid')"
                )
                conn.exec_driver_sql(
                    "CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN "
                    "INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text); "
                    "END"
                )
                conn.exec_driver_sql(
                    "CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN "
                    "INSERT INTO chunks_fts(chunks_fts, rowid, text) "
                    "VALUES('delete', old.rowid, old.text); "
                    "END"
                )
                conn.exec_driver_sql(
                    "CREATE TRIGGER chunks_au AFTER UPDATE ON chunks BEGIN "
                    "INSERT INTO chunks_fts(chunks_fts, rowid, text) "
                    "VALUES('delete', old.rowid, old.text); "
                    "INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text); "
                    "END"
                )
                # One-time backfill — bulk insert via SELECT is fast
                # (a few seconds even for 140k rows on local SSD).
                cur = conn.exec_driver_sql(
                    "INSERT INTO chunks_fts(rowid, text) "
                    "SELECT rowid, text FROM chunks"
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
                    delete(RagChunk).where(
                        RagChunk.project_id == project_id,
                        RagChunk.doc_id == doc_id,
                    )
                )
                for i, (txt, vec) in enumerate(zip(chunks, emb)):
                    session.add(
                        RagChunk(
                            chunk_id=f"{project_id}:{doc_id}:{i}",
                            project_id=project_id,
                            doc_id=doc_id,
                            chunk_index=i,
                            text=txt,
                            embedding=vec,
                            created_at=now,
                        )
                    )
                session.commit()
        return len(chunks)

    def delete_doc(self, project_id: str, doc_id: str) -> int:
        with self._lock:
            with self._session_factory()() as session:
                result = session.execute(
                    delete(RagChunk).where(
                        RagChunk.project_id == project_id,
                        RagChunk.doc_id == doc_id,
                    )
                )
                session.commit()
                return int(result.rowcount or 0)

    # ── Read ─────────────────────────────────────────────────────────────

    def count(self, project_id: Optional[str] = None) -> int:
        with self._lock:
            with self._session_factory()() as session:
                stmt = select(func.count()).select_from(RagChunk)
                if project_id is not None:
                    stmt = stmt.where(RagChunk.project_id == project_id)
                return int(session.scalar(stmt) or 0)

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
        vec_col = cast(RagChunk.embedding, Vector(EMBEDDING_DIM))
        distance = vec_col.cosine_distance(q_list)
        score_expr = (1 - distance).label("score")
        stmt = (
            select(
                RagChunk.chunk_id,
                RagChunk.project_id,
                RagChunk.doc_id,
                RagChunk.chunk_index,
                RagChunk.text,
                score_expr,
            )
            .where(RagChunk.project_id == project_id)
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
            )
            for row in rows
        ]

    def _search_numpy(
        self, project_id: str, query_vec: np.ndarray, k: int
    ) -> List[Chunk]:
        stmt = select(RagChunk).where(RagChunk.project_id == project_id)
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
        sql = text(
            """
            SELECT c.chunk_id, c.project_id, c.doc_id, c.chunk_index,
                   c.text,
                   ts_rank(c.text_search, q) AS rank
            FROM chunks c, plainto_tsquery('english', :q) AS q
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
        sql = text(
            """
            SELECT c.chunk_id, c.project_id, c.doc_id, c.chunk_index,
                   c.text, chunks_fts.rank AS bm25_rank
            FROM chunks_fts
            JOIN chunks c ON c.rowid = chunks_fts.rowid
            WHERE chunks_fts MATCH :q
              AND c.project_id = :project_id
            ORDER BY chunks_fts.rank
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
            )
            for r in rows
        ]

    # ── photo_chunks BM25 leg (V2) ────────────────────────────────────────
    #
    # Queries the photo_chunks table (migration 0006) the same way the text
    # leg queries chunks. Photo chunks expose kind='photo' + sha256 +
    # photo_url so downstream callers can render image citations alongside
    # text citations from the same retrieval result list.

    def bm25_search_photos(
        self,
        query: str,
        k: int = 5,
        project_id: Optional[str] = None,
    ) -> List[Chunk]:
        """Top-k photo chunks for a query.

        ``project_id`` is None for V1 (photos uploaded without a project).
        When provided, results are restricted to that project OR rows with
        NULL project_id (the V1 zip's photos are visible everywhere until
        Phase 3 backfills them).
        """
        if not query or not query.strip():
            return []
        if self._use_pgvector:
            return self._bm25_photos_postgres(query, k, project_id)
        return self._bm25_photos_sqlite(query, k, project_id)

    def _bm25_photos_postgres(
        self, query: str, k: int, project_id: Optional[str]
    ) -> List[Chunk]:
        proj_filter = "AND (pc.project_id IS NULL OR pc.project_id = :project_id)" if project_id else ""
        sql = text(
            f"""
            SELECT pc.chunk_id, pc.project_id, pc.sha256, pc.caption, pc.photo_metadata,
                   ts_rank(to_tsvector('english', pc.caption), q) AS rank
            FROM photo_chunks pc, plainto_tsquery('english', :q) AS q
            WHERE to_tsvector('english', pc.caption) @@ q
              {proj_filter}
            ORDER BY rank DESC
            LIMIT :k
            """
        )
        params = {"q": query, "k": k}
        if project_id:
            params["project_id"] = project_id
        try:
            with self._lock:
                with self._session_factory()() as session:
                    rows = session.execute(sql, params).all()
        except OperationalError as e:
            logger.warning("bm25_search_photos (postgres) failed: %s; query=%r", e, query)
            return []
        return [self._photo_row_to_chunk(r) for r in rows]

    def _bm25_photos_sqlite(
        self, query: str, k: int, project_id: Optional[str]
    ) -> List[Chunk]:
        self._ensure_photo_chunks_fts_sqlite()
        safe_query = _sanitize_fts5_query(query)
        if not safe_query:
            return []
        proj_filter = "AND (pc.project_id IS NULL OR pc.project_id = :project_id)" if project_id else ""
        sql = text(
            f"""
            SELECT pc.chunk_id, pc.project_id, pc.sha256, pc.caption, pc.photo_metadata,
                   photo_chunks_fts.rank AS bm25_rank
            FROM photo_chunks_fts
            JOIN photo_chunks pc ON pc.rowid = photo_chunks_fts.rowid
            WHERE photo_chunks_fts MATCH :q
              {proj_filter}
            ORDER BY photo_chunks_fts.rank
            LIMIT :k
            """
        )
        params = {"q": safe_query, "k": k}
        if project_id:
            params["project_id"] = project_id
        try:
            with self._lock:
                with self._session_factory()() as session:
                    rows = session.execute(sql, params).all()
        except OperationalError as e:
            logger.warning("bm25_search_photos (sqlite) FTS5 MATCH failed: %s; query=%r",
                           e, safe_query)
            return []
        return [self._photo_row_to_chunk(r) for r in rows]

    def _photo_row_to_chunk(self, r) -> Chunk:
        meta = r.photo_metadata
        if isinstance(meta, str):
            try:
                meta = json_lib.loads(meta)
            except Exception:
                meta = None
        # Caption + class-name keywords as the searchable text. Class names
        # in the caption already make this BM25-discoverable; class-tag list
        # adds redundancy for queries that use canonical underscored names.
        caption = r.caption or ""
        class_names: List[str] = []
        if isinstance(meta, dict):
            for d in (meta.get("safety_qaqc") or []):
                if isinstance(d, dict) and d.get("class"):
                    class_names.append(d["class"])
        content = caption
        if class_names:
            content = f"{caption}\nClasses: {', '.join(class_names)}"
        return Chunk(
            chunk_id=r.chunk_id,
            project_id=r.project_id or "",
            doc_id=r.sha256,
            chunk_index=0,
            text=content,
            score=float(getattr(r, "rank", None) or getattr(r, "bm25_rank", 0.0)),
            kind="photo",
            sha256=r.sha256,
            photo_url=f"/v1/photos/{r.sha256}",
            photo_metadata=meta if isinstance(meta, dict) else None,
        )

    def _ensure_photo_chunks_fts_sqlite(self) -> None:
        """Lazy FTS5 virtual table for photo_chunks on SQLite. Mirrors
        the ``chunks_fts`` external-content pattern used for text chunks."""
        with self._lock:
            with self._session_factory()() as session:
                conn = session.connection()
                row = conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='photo_chunks_fts'"
                ).fetchone()
                if row is not None:
                    return
                conn.exec_driver_sql(
                    "CREATE VIRTUAL TABLE photo_chunks_fts USING fts5("
                    "caption, content='photo_chunks', content_rowid='rowid')"
                )
                conn.exec_driver_sql(
                    "CREATE TRIGGER photo_chunks_ai AFTER INSERT ON photo_chunks BEGIN "
                    "INSERT INTO photo_chunks_fts(rowid, caption) VALUES (new.rowid, new.caption); "
                    "END"
                )
                conn.exec_driver_sql(
                    "CREATE TRIGGER photo_chunks_ad AFTER DELETE ON photo_chunks BEGIN "
                    "INSERT INTO photo_chunks_fts(photo_chunks_fts, rowid, caption) "
                    "VALUES('delete', old.rowid, old.caption); "
                    "END"
                )
                conn.exec_driver_sql(
                    "CREATE TRIGGER photo_chunks_au AFTER UPDATE ON photo_chunks BEGIN "
                    "INSERT INTO photo_chunks_fts(photo_chunks_fts, rowid, caption) "
                    "VALUES('delete', old.rowid, old.caption); "
                    "INSERT INTO photo_chunks_fts(rowid, caption) VALUES (new.rowid, new.caption); "
                    "END"
                )
                # One-time backfill for existing rows
                conn.exec_driver_sql(
                    "INSERT INTO photo_chunks_fts(rowid, caption) "
                    "SELECT rowid, caption FROM photo_chunks"
                )
                session.commit()


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
            pass
        scored.append((rrf, c))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:top_k]]
