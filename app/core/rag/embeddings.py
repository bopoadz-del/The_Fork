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

# MiniLM's hidden size. The fake-embedder mode and sentence-transformers
# path both use this. model2vec/potion-base-8M produces 256-dim vectors
# instead; the Embedder reports its actual dim via .dim so the vector
# store initializes with the matching size.
EMBEDDING_DIM = 384
EMBEDDING_DIM_MODEL2VEC = 256

# Default model — the only "real" sentence-transformers one we promise
# to support. When sentence-transformers is missing but model2vec is
# installed (the slim production image case), we use model2vec instead.
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_MODEL2VEC = "minishlab/potion-base-8M"


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
    """Wraps a real semantic embedding model (or fake mode for tests).

    Backend resolution order (lazy, first hit wins):

    1. fake — when ``model_name="fake"``. Deterministic, no model load.
    2. sentence-transformers — when installed (requirements-rag.txt).
       Produces 384-dim MiniLM vectors. Adds ~1.5 GB of torch deps to
       the image; not in the slim production build.
    3. model2vec — when installed (requirements.txt baseline). Produces
       256-dim potion-base-8M vectors. ~80 MB, no torch. This is the
       production path on the slim Render image.
    4. raises RuntimeError if none of the above is available.

    Public surface:

    * :attr:`dim` — vector dimension (384 for fake / sentence-transformers,
      256 for model2vec). Inspect AFTER construction so the vector store
      can size itself to the actual backend.
    * :meth:`encode(texts)` — returns L2-normalized ``np.ndarray`` of shape
      ``(len(texts), dim)``. Normalization is on so cosine similarity is a
      pure dot product downstream.
    * :meth:`available` (static) — True when ANY real backend is
      importable (sentence-transformers OR model2vec). Always True for
      fake mode.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.model_name = model_name
        self._fake = (model_name == "fake")
        self._model = None
        self._backend: Optional[str] = None  # "fake" | "sentence_transformers" | "model2vec"
        self._dim: int = EMBEDDING_DIM
        if self._fake:
            self._backend = "fake"
            self._dim = EMBEDDING_DIM
            return
        # Backend selection at construction time so .dim is stable.
        if _has_sentence_transformers():
            self._backend = "sentence_transformers"
            self._dim = EMBEDDING_DIM
        elif _has_model2vec():
            self._backend = "model2vec"
            self._dim = EMBEDDING_DIM_MODEL2VEC
        else:
            raise RuntimeError(
                "No semantic embedding backend available. Install "
                "sentence-transformers (requirements-rag.txt) OR model2vec "
                "(in baseline requirements.txt) -- or pass model_name='fake'."
            )

    @staticmethod
    def available() -> bool:
        """True when ANY real embedding backend can be loaded."""
        return _has_sentence_transformers() or _has_model2vec()

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def backend(self) -> Optional[str]:
        return self._backend

    def encode(self, texts: List[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        if self._backend == "fake":
            return np.array([_fake_embedding(t) for t in texts], dtype=np.float32)
        if self._model is None:
            self._load_model()
        if self._backend == "sentence_transformers":
            vecs = self._model.encode(
                texts,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        else:  # model2vec
            vecs = self._model.encode(texts)
            # model2vec doesn't normalize by default — do it here so cosine
            # similarity stays a pure dot product downstream.
            arr = np.asarray(vecs, dtype=np.float32)
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1.0, norms)
            return (arr / norms).astype(np.float32)
        return vecs.astype(np.float32)

    def _load_model(self) -> None:
        if self._backend == "sentence_transformers":
            from sentence_transformers import SentenceTransformer
            logger.info("Loading sentence-transformers model: %s", self.model_name)
            self._model = SentenceTransformer(self.model_name)
        elif self._backend == "model2vec":
            from model2vec import StaticModel
            logger.info("Loading model2vec model: %s", DEFAULT_MODEL2VEC)
            self._model = StaticModel.from_pretrained(DEFAULT_MODEL2VEC)


def _has_sentence_transformers() -> bool:
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False


def _has_model2vec() -> bool:
    try:
        import model2vec  # noqa: F401
        return True
    except ImportError:
        return False


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
