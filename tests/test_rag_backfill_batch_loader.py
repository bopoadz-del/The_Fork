"""Unit tests for Render batch RAG backfill loader verify behavior."""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

from scripts.rag_backfill_batch_loader import load_payload


def _payload(docs: List[Dict[str, Any]], dim: int = 4) -> Dict[str, Any]:
    return {
        "project_id": "proj_1",
        "embedding_model": "fake-model",
        "embedding_dim": dim,
        "docs": docs,
    }


def _doc(doc_id: str, n_chunks: int, dim: int = 4) -> Dict[str, Any]:
    return {
        "doc_id": doc_id,
        "project_id": "proj_1",
        "chunk_count": n_chunks,
        "chunks": [
            {"text": f"chunk-{i}", "embedding": [0.1] * dim}
            for i in range(n_chunks)
        ],
    }


def test_verify_mismatch_records_error_and_non_zero_exit_signal():
    store = MagicMock()
    store.dim = 4
    store.model_name = "fake-model"
    store.upsert_chunks.return_value = 2
    # Stored count differs from payload chunk_count → VERIFY FAIL
    store.count_by_doc.return_value = {"doc_a": 1}

    with patch("scripts.rag_backfill_batch_loader.get_store", return_value=store):
        results = load_payload(_payload([_doc("doc_a", 2)]))

    assert results["successful"] == 1
    assert len(results["errors"]) == 1
    assert results["errors"][0]["doc_id"] == "doc_a"
    assert "VERIFY FAIL" in results["errors"][0]["error"]
    # main() returns 0 iff not results["errors"]
    assert (0 if not results["errors"] else 1) == 1


def test_verify_ok_leaves_errors_empty():
    store = MagicMock()
    store.dim = 4
    store.model_name = "fake-model"
    store.upsert_chunks.return_value = 2
    store.count_by_doc.return_value = {"doc_a": 2}

    with patch("scripts.rag_backfill_batch_loader.get_store", return_value=store):
        results = load_payload(_payload([_doc("doc_a", 2)]))

    assert results["successful"] == 1
    assert results["errors"] == []
    assert (0 if not results["errors"] else 1) == 0


def test_platform_index_imports_core_db_not_app_db():
    """Guard against regressing to the non-existent app.db package."""
    from pathlib import Path

    src = Path("scripts/rag_backfill_platform_index.py").read_text(encoding="utf-8")
    assert "app.db" not in src
    assert "from app.core.db import SessionLocal" in src
    assert "from app.core.models import Document" in src
