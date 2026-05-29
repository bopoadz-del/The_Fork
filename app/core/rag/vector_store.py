"""Per-project chunk store backed by SQLite.

Storage shape: one row per chunk in the ``chunks`` table. When the
``sqlite-vec`` extension loads cleanly, we also mirror embeddings into a
``vec_chunks`` virtual table for ANN search. When the extension is
missing or the host's SQLite was built without extension support, the
search method falls back to numpy cosine similarity over all chunks for
the project — slower (linear in chunk count), but works everywhere and
is fine for project sizes under a few thousand chunks.

The fallback is not "degraded" — it's a real implementation. We just
trade scan time for not needing the C extension. Production deployments
on x86 Linux will get the fast path; CI and Mac dev machines may take
the slow path. Both return the same chunks in the same order.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, asdict
from threading import Lock
from typing import Iterable, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


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


# ── Module-level cache (one store per db_path) ────────────────────────────

_STORE_CACHE: dict = {}
_CACHE_LOCK = Lock()


def get_store(dim: int = 384, db_path: Optional[str] = None) -> "VectorStore":
    """Process-cached store. Different ``db_path`` values get different
    cached instances, which keeps tests isolated when they swap DATA_DIR."""
    key = (db_path or _default_db_path(), dim)
    with _CACHE_LOCK:
        if key not in _STORE_CACHE:
            _STORE_CACHE[key] = VectorStore(db_path=key[0], dim=dim)
    return _STORE_CACHE[key]


def reset_store_cache() -> None:
    """Drop all cached stores. Used by tests to pick up a swapped DATA_DIR."""
    global _STORE_CACHE
    with _CACHE_LOCK:
        for s in _STORE_CACHE.values():
            try:
                s.close()
            except Exception:
                pass
        _STORE_CACHE = {}


def _default_db_path() -> str:
    base = os.getenv("DATA_DIR", "./data")
    d = os.path.join(base, "rag")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "vectors.db")


# ── Store ────────────────────────────────────────────────────────────────


class VectorStore:
    """SQLite-backed chunk store with optional ``sqlite-vec`` acceleration.

    Thread-safety: one connection per instance, guarded by an internal
    lock. Sufficient for the chat path (one query per request); not
    designed for massive parallel ingest.
    """

    def __init__(self, db_path: str, dim: int = 384):
        self.db_path = db_path
        self.dim = dim
        self._lock = Lock()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._vec_available = self._try_load_vec()
        self._init_schema()

    # ── Setup ────────────────────────────────────────────────────────────

    def _try_load_vec(self) -> bool:
        """Was: probe whether sqlite-vec can load and surface that via
        ``fast_search``. As of the PRs #19-#23 retrospective, the search
        path was already numpy-only (the "10k chunks" threshold comment
        below in search() explains why), and the write path was populating
        ``vec_chunks`` for nothing — pure overhead including a stable-rowid
        IntegrityError dance to maintain ordering.

        Now forced to ``False``. ``fast_search`` always reports False to
        match reality (the field stays in the API response schema at
        ``app/routers/rag.py:40,87`` so removing it would be a breaking
        change). Re-enable by reverting this method when search() is
        actually wired to vec0 with a post-filter — at that point the
        write path needs to come back too.

        See ``docs/SECURITY_TRIAGE.md`` for the rationale trail.
        """
        return False

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id     TEXT PRIMARY KEY,
                    project_id   TEXT NOT NULL,
                    doc_id       TEXT NOT NULL,
                    chunk_index  INTEGER NOT NULL,
                    text         TEXT NOT NULL,
                    embedding    BLOB NOT NULL,
                    created_at   TEXT NOT NULL,
                    UNIQUE(project_id, doc_id, chunk_index)
                )
            """)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_project ON chunks(project_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(project_id, doc_id)"
            )
            if self._vec_available:
                self._conn.execute(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks "
                    f"USING vec0(embedding float[{self.dim}])"
                )

    @property
    def fast_search(self) -> bool:
        """True when sqlite-vec is loaded and search uses ANN."""
        return self._vec_available

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

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

        # Embeddings as float32 for storage; assert dim
        emb = np.asarray(embeddings, dtype=np.float32)
        if emb.shape[1] != self.dim:
            raise ValueError(
                f"embedding dim {emb.shape[1]} != store dim {self.dim}"
            )

        now = _now()
        with self._lock, self._conn:
            self._delete_doc_locked(project_id, doc_id)
            rows = []
            vec_rows = []
            for i, (text, vec) in enumerate(zip(chunks, emb)):
                chunk_id = f"{project_id}:{doc_id}:{i}"
                blob = vec.tobytes()
                rows.append((chunk_id, project_id, doc_id, i, text, blob, now))
                vec_rows.append((chunk_id, vec))
            self._conn.executemany(
                "INSERT INTO chunks "
                "(chunk_id, project_id, doc_id, chunk_index, text, embedding, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            # Dead-write removal (PRs #19-#23 retro): the vec_chunks
            # virtual-table mirror was populated here but never read by
            # search(). Now _try_load_vec returns False so _vec_available
            # is False and this branch never ran anyway — removing the
            # code to make the intent explicit. When search() is wired to
            # vec0, restore the upsert here in lock-step with the read.
        return len(chunks)

    def delete_doc(self, project_id: str, doc_id: str) -> int:
        with self._lock, self._conn:
            return self._delete_doc_locked(project_id, doc_id)

    def _delete_doc_locked(self, project_id: str, doc_id: str) -> int:
        # Dead-write removal (PRs #19-#23 retro): the vec_chunks delete
        # mirror is gone alongside the upsert mirror. Restore in lock-step
        # with the upsert when search() is wired to vec0.
        cur = self._conn.execute(
            "DELETE FROM chunks WHERE project_id = ? AND doc_id = ?",
            (project_id, doc_id),
        )
        return cur.rowcount

    # ── Read ─────────────────────────────────────────────────────────────

    def count(self, project_id: Optional[str] = None) -> int:
        with self._lock, self._conn:
            if project_id is None:
                cur = self._conn.execute("SELECT COUNT(*) AS n FROM chunks")
            else:
                cur = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM chunks WHERE project_id = ?",
                    (project_id,),
                )
            return int(cur.fetchone()["n"])

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
            raise ValueError(f"query_vec must be 1-D of length {self.dim}; got shape {q.shape}")

        with self._lock, self._conn:
            # Fast path is per-project filtered ANN. sqlite-vec's MATCH
            # is global; we need to combine with project_id which means
            # joining back to chunks. Easier and almost as fast for our
            # scale: scan all project chunks via numpy. We only use the
            # vec0 path when project_id is None (rare; admin queries).
            #
            # For PR 2 we keep search uniform — numpy cosine over the
            # project's chunks. When chunk counts pass ~10k per project,
            # rework this to use vec0 with a post-filter.
            rows = self._conn.execute(
                "SELECT chunk_id, project_id, doc_id, chunk_index, text, embedding "
                "FROM chunks WHERE project_id = ?",
                (project_id,),
            ).fetchall()

        if not rows:
            return []

        embs = np.stack([
            np.frombuffer(r["embedding"], dtype=np.float32) for r in rows
        ])
        # Embeddings are pre-normalized by Embedder.encode; query is too.
        # So dot product = cosine. If a caller passes an unnormalized
        # query we degrade gracefully but the score scale shifts.
        sims = embs @ q
        order = np.argsort(-sims)[:k]
        out: List[Chunk] = []
        for idx in order:
            r = rows[int(idx)]
            out.append(Chunk(
                chunk_id=r["chunk_id"],
                project_id=r["project_id"],
                doc_id=r["doc_id"],
                chunk_index=int(r["chunk_index"]),
                text=r["text"],
                score=float(sims[int(idx)]),
            ))
        return out


# ── Helpers ───────────────────────────────────────────────────────────────


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# _stable_rowid removed in PRs #19-#23 retro — its only callers were the
# vec_chunks write/delete mirrors which are now gone. Restore alongside
# the read path if/when search() is wired to vec0.
