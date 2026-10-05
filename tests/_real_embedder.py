"""Run a test on the REAL semantic embedder instead of CI's fake one.

CI pins ``RAG_EMBEDDING_MODEL=fake``: a deterministic but meaningless
hash-of-text vector. Any assertion about which document ranks FIRST is noise
under it -- the retriever orders candidates by cosine, so "concrete curing"
ranking concrete.txt above landscaping.txt is a coin toss decided by sha256.

``use_real_embedder`` switches the calling test to ``minishlab/potion-base-8M``
(model2vec, already in requirements.txt), loaded from the local HF cache with
the Hub forced offline, so nothing downloads inside a test. Every CI test job
prefetches the model and sets ``RAG_RANKING_MODEL_REQUIRED=1``: there, an
unloadable model FAILS the test instead of skipping it. Without the flag (a
bare local checkout) the test skips with the reason.

The per-test vector namespace from ``conftest._reset_rag_caches`` keeps the
real model's identity stamp away from every fake-embedded test.
"""
from __future__ import annotations

import os

import pytest

from app.core.rag.embeddings import (
    DEFAULT_MODEL2VEC,
    Embedder,
    reset_embedder_cache,
)

RANKING_MODEL = DEFAULT_MODEL2VEC


def use_real_embedder(monkeypatch) -> str:
    """Point ``get_embedder()`` at the real model for the rest of the test."""
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    try:
        import huggingface_hub.constants as _hf_constants
    except ImportError:
        _hf_constants = None
    if _hf_constants is not None:
        # huggingface_hub reads the env once at import; patch the live value.
        monkeypatch.setattr(_hf_constants, "HF_HUB_OFFLINE", True, raising=False)
    try:
        probe = Embedder(model_name=RANKING_MODEL)
    except Exception as exc:  # noqa: BLE001 -- not installed / not cached
        msg = (f"real embedder {RANKING_MODEL!r} unavailable offline "
               f"({type(exc).__name__}: {exc})")
        if os.getenv("RAG_RANKING_MODEL_REQUIRED", "").strip() == "1":
            pytest.fail(msg + " -- RAG_RANKING_MODEL_REQUIRED=1, so this run "
                        "must provide it (see test.yml prefetch step)")
        pytest.skip(msg)
    assert probe.backend != "fake", probe.backend
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", RANKING_MODEL)
    reset_embedder_cache()
    from app.core.rag import vector_store as _vs
    _vs.reset_store_cache()
    return RANKING_MODEL
