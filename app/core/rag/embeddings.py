"""Embedding model wrapper — sentence-transformers/all-MiniLM-L6-v2.

Why MiniLM? 384 dims (small index size), ~25 MB model, ~80 MB tokenizer +
vocab, CPU-runnable in ~10 ms per sentence. Good enough for retrieval
against construction project docs; not state-of-the-art but fits the
roadmap's "small + fast" rubric. Swap is one line if you want all-mpnet-
base-v2 later.

Test fixture: pass ``model_name="fake"`` to get a deterministic
hash-based embedder that produces 384-dim vectors without loading any
model. Used throughout ``tests/test_rag.py`` so the suite has no model
download dependency.
"""

from __future__ import annotations

import hashlib
import logging
import os
from threading import Lock
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# MiniLM's hidden size. Hardcoded because the fake-embedder mode needs
# the same dim and changing the model is a separate decision.
EMBEDDING_DIM = 384

# Default model — the only "real" one we promise to support. Other
# sentence-transformers models work too but aren't tested.
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


# ── Module-level cache ────────────────────────────────────────────────────
# One embedder per process. Loading the model is slow (~1-2 s) and the
# weights are large enough that we don't want multiple copies in RAM.

_EMBEDDER_CACHE: Optional["Embedder"] = None
_CACHE_LOCK = Lock()


def get_embedder(model_name: Optional[str] = None) -> "Embedder":
    """Return the process-cached embedder, creating it on first call.

    ``model_name=None`` honors the ``RAG_EMBEDDING_MODEL`` env var, then
    falls back to ``DEFAULT_MODEL``. Pass ``"fake"`` for tests.
    """
    global _EMBEDDER_CACHE
    name = model_name or os.getenv("RAG_EMBEDDING_MODEL") or DEFAULT_MODEL
    with _CACHE_LOCK:
        if _EMBEDDER_CACHE is None or _EMBEDDER_CACHE.model_name != name:
            _EMBEDDER_CACHE = Embedder(model_name=name)
    return _EMBEDDER_CACHE


def reset_embedder_cache() -> None:
    """Drop the cached embedder. Used by tests to swap fake/real cleanly."""
    global _EMBEDDER_CACHE
    with _CACHE_LOCK:
        _EMBEDDER_CACHE = None


class Embedder:
    """Wraps a sentence-transformers model (or the fake mode for tests).

    Public surface:

    * :attr:`dim` — vector dimension (384)
    * :meth:`encode(texts)` — returns L2-normalized ``np.ndarray`` of shape
      ``(len(texts), dim)``. Normalization is on so cosine similarity is a
      pure dot product downstream.
    * :meth:`available` (static) — True when sentence-transformers is
      importable. Always True for fake mode.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.model_name = model_name
        self._fake = (model_name == "fake")
        self._model = None  # lazy-loaded on first encode for real models
        if not self._fake and not self.available():
            raise RuntimeError(
                "sentence-transformers is not installed. Install with "
                "`pip install -r requirements-rag.txt` or pass model_name='fake'."
            )

    @staticmethod
    def available() -> bool:
        """True when the real embedding stack can be loaded."""
        try:
            import sentence_transformers  # noqa: F401
            return True
        except ImportError:
            return False

    @property
    def dim(self) -> int:
        return EMBEDDING_DIM

    def encode(self, texts: List[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        if self._fake:
            return np.array([_fake_embedding(t) for t in texts], dtype=np.float32)
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading embedding model: %s", self.model_name)
            self._model = SentenceTransformer(self.model_name)
        vecs = self._model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return vecs.astype(np.float32)


def _fake_embedding(text: str) -> np.ndarray:
    """Deterministic 384-dim L2-normalized vector derived from text hash.

    Lets tests assert "same input → same vector" and "different input →
    different vector" without any model file. Two inputs produce
    identical vectors iff their SHA-256 digests match.

    Not a real semantic embedder — it's a hashing trick. Texts that
    share substrings get vectors that are mostly orthogonal, which is
    actually a useful property for tests: it makes accidental collisions
    rare and similarity scores meaningful in toy datasets.
    """
    h = hashlib.sha256(text.encode("utf-8")).digest()
    # Expand 32 bytes to 384 floats by repeated hashing.
    buf = bytearray()
    seed = h
    while len(buf) < EMBEDDING_DIM * 4:
        seed = hashlib.sha256(seed).digest()
        buf.extend(seed)
    arr = np.frombuffer(bytes(buf[: EMBEDDING_DIM * 4]), dtype=np.uint32).astype(np.float32)
    # Shift to [-1, 1] range so vectors aren't all in one quadrant
    arr = (arr / (2**32 - 1)) * 2.0 - 1.0
    # L2 normalize so cosine = dot product
    norm = np.linalg.norm(arr)
    if norm > 0:
        arr = arr / norm
    return arr
