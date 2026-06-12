"""Per-project chunk store backed by SQLAlchemy.

Storage shape: one row per chunk in the ``chunks`` table. On PostgreSQL
embeddings use ``pgvector`` ``vector(256)`` with cosine-distance ANN search
(``ORDER BY embedding <=> :q`` scoped by ``project_id``). On SQLite the
same table stores float32 BLOBs and search falls back to numpy cosine
similarity over the project's rows — slower but works everywhere.

SQLAlchemy-backed via app.core.db — unified The Fork schema.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from threading import Lock
from typing import List, Optional, Set

import numpy as np
from pgvector.sqlalchemy import Vector
from sqlalchemy import cast, delete, func, select
from sqlalchemy.orm import Session

from app.core.db import _engine_for_url, _session_factory_for_url, get_database_url
from app.core.models import EMBEDDING_DIM, Document, Project, RagChunk

# ── Public types ──────────────────────────────────────────────────────────


@dataclass
class Chunk:
    """One indexed chunk. ``embedding`` is omitted from the public
    serializer to keep response payloads small."""

    chunk_id: str
    project_id: str
    doc_id: str
    chunk_index: int
    text: str
    score: Optional[float] = None  # set on search results, None when raw

    def to_dict(self) -> dict:
        d = asdict(self)
        # Drop None scores from API responses
        if d["score"] is None:
            d.pop("score")
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
                for i, (text, vec) in enumerate(zip(chunks, emb)):
                    session.add(
                        RagChunk(
                            chunk_id=f"{project_id}:{doc_id}:{i}",
                            project_id=project_id,
                            doc_id=doc_id,
                            chunk_index=i,
                            text=text,
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
    ) -> List[Chunk]:
        """Top-``k`` chunks for ``project_id`` ranked by cosine similarity.

        Returns an empty list when the project has no indexed chunks.
        Scores are L2-normalized cosine (range [-1, 1], ~1 = best).
        """
        q = np.asarray(query_vec, dtype=np.float32)
        if q.ndim != 1 or q.shape[0] != self.dim:
            raise ValueError(
                f"query_vec must be 1-D of length {self.dim}; got shape {q.shape}"
            )

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


# ── Helpers ───────────────────────────────────────────────────────────────


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
