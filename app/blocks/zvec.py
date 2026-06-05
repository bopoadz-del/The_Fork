"""Zvec block — semantic embeddings + cosine similarity + zero-shot classify.

Embedding backend selection (lazy, module-level cache):

1. **model2vec** if installed — small static distilled model (~80 MB
   total, no torch), produces real semantic 256-dim vectors. This is the
   path the runtime image installs.
2. **sentence-transformers** if installed — full MiniLM (~1.5 GB with
   torch). Used when present, never auto-installed.
3. **TF-IDF fallback** — scikit-learn. Useful for similarity within a
   corpus, but for a SINGLE input text returns a uniform vector by
   construction (every n-gram has the same TF and IDF). Kept for
   backwards-compat and as a similarity workhorse.

Previous behaviour was TF-IDF-only, which made the operation `embed`
return a uniform `1/sqrt(d)` vector for any single text — useless for
semantic search. With model2vec, the same call returns a real semantic
vector that clusters meaningfully.
"""

import threading
from typing import Any, Dict, List, Optional

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.core.universal_base import UniversalBlock

# Lazy module-level cache so the model loads once per process.
_SEMANTIC_LOCK = threading.Lock()
_SEMANTIC_MODEL = None
_SEMANTIC_BACKEND: Optional[str] = None  # "model2vec" | "sentence_transformers" | None


def _get_semantic_model():
    """Lazy-load and cache the best available semantic embedding model.

    Returns ``(model, backend_name)`` or ``(None, None)`` if no semantic
    backend is installed.
    """
    global _SEMANTIC_MODEL, _SEMANTIC_BACKEND
    if _SEMANTIC_MODEL is not None or _SEMANTIC_BACKEND == "missing":
        return _SEMANTIC_MODEL, _SEMANTIC_BACKEND if _SEMANTIC_BACKEND != "missing" else None

    with _SEMANTIC_LOCK:
        if _SEMANTIC_MODEL is not None:
            return _SEMANTIC_MODEL, _SEMANTIC_BACKEND
        # model2vec: small, no torch.
        try:
            from model2vec import StaticModel
            _SEMANTIC_MODEL = StaticModel.from_pretrained("minishlab/potion-base-8M")
            _SEMANTIC_BACKEND = "model2vec"
            return _SEMANTIC_MODEL, _SEMANTIC_BACKEND
        except Exception:
            pass
        # sentence-transformers (heavy, only if explicitly installed).
        try:
            from sentence_transformers import SentenceTransformer
            _SEMANTIC_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
            _SEMANTIC_BACKEND = "sentence_transformers"
            return _SEMANTIC_MODEL, _SEMANTIC_BACKEND
        except Exception:
            pass
        _SEMANTIC_BACKEND = "missing"
        return None, None


def _semantic_encode(texts: List[str]) -> Optional[np.ndarray]:
    """Return an (n, d) float array of L2-normalized semantic vectors,
    or None if no semantic backend is available.
    """
    model, backend = _get_semantic_model()
    if model is None:
        return None
    if backend == "model2vec":
        vecs = model.encode(texts)
    else:  # sentence_transformers
        vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    arr = np.asarray(vecs, dtype=np.float32)
    # Defensive L2 normalize (model2vec already returns normalized but be safe).
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


def _vectorize(texts: List[str], char_level: bool = False) -> np.ndarray:
    if char_level:
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), max_features=512)
    else:
        vec = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), max_features=512, sublinear_tf=True)
    return vec.fit_transform(texts).toarray()


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(cosine_similarity(a.reshape(1, -1), b.reshape(1, -1))[0][0])


class ZvecBlock(UniversalBlock):
    """TF-IDF vector embeddings, similarity, and zero-shot classification"""

    name = "zvec"
    version = "2.0"
    description = "Embed text as TF-IDF vectors; compute similarity; zero-shot classify"
    layer = 2
    tags = ["ai", "core", "vector", "zero-shot"]
    requires = []

    ui_schema = {
        "input": {
            "type": "text",
            "accept": None,
            "placeholder": "Text to embed or compare...",
            "multiline": False,
        },
        "output": {
            "type": "json",
            "fields": [
                {"name": "vector", "type": "array", "label": "Embedding"},
                {"name": "operation", "type": "text", "label": "Operation"},
            ],
        },
        "quick_actions": [
            {"icon": "⚡", "label": "Vectorize", "prompt": "Convert to vector embedding"}
        ],
    }

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        operation = params.get("operation", "embed")

        text = ""
        _input_text_a = ""
        _input_text_b = ""
        if isinstance(input_data, str):
            text = input_data
        elif isinstance(input_data, dict):
            text = (input_data.get("text") or input_data.get("query") or
                    input_data.get("input") or "")
            _input_text_a = input_data.get("text_a", "")
            _input_text_b = input_data.get("text_b", "")
            if not text:
                text = _input_text_a or params.get("text", "")
        else:
            text = params.get("text", "")

        if not text and operation not in ("similarity", "batch_embed"):
            return {"status": "error", "error": "Text input required"}

        try:
            if operation == "embed":
                # Prefer semantic (model2vec) — TF-IDF on a single text
                # returns a uniform vector by construction.
                semantic = _semantic_encode([text])
                if semantic is not None:
                    vec = semantic[0].tolist()
                    return {
                        "status": "success",
                        "operation": "embed",
                        "backend": _SEMANTIC_BACKEND,
                        "vector": vec,
                        "dimensions": len(vec),
                        "text": text[:200],
                    }
                # Fallback: TF-IDF (caller may not get semantic similarity).
                vectors = _vectorize([text])
                vec = vectors[0].tolist()
                return {
                    "status": "success",
                    "operation": "embed",
                    "backend": "tfidf_fallback",
                    "vector": vec,
                    "dimensions": len(vec),
                    "text": text[:200],
                    "warning": (
                        "model2vec not installed — fell back to TF-IDF which "
                        "returns a uniform vector for a single text. Install "
                        "model2vec or sentence-transformers for semantic embeddings."
                    ),
                }

            elif operation == "batch_embed":
                texts = params.get("texts", [text] if text else [])
                if not texts:
                    return {"status": "error", "error": "Provide 'texts' list in params"}
                semantic = _semantic_encode(texts)
                if semantic is not None:
                    return {
                        "status": "success",
                        "operation": "batch_embed",
                        "backend": _SEMANTIC_BACKEND,
                        "embeddings": [v.tolist() for v in semantic],
                        "dimensions": semantic.shape[1],
                        "count": len(texts),
                    }
                vectors = _vectorize(texts)
                return {
                    "status": "success",
                    "operation": "batch_embed",
                    "backend": "tfidf_fallback",
                    "embeddings": [v.tolist() for v in vectors],
                    "dimensions": vectors.shape[1],
                    "count": len(texts),
                }

            elif operation == "similarity":
                text_a = params.get("text_a") or _input_text_a or text
                text_b = params.get("text_b") or _input_text_b or ""
                texts_list = params.get("texts", [])

                if texts_list and len(texts_list) >= 2:
                    vectors = _vectorize(texts_list)
                    matrix = cosine_similarity(vectors).tolist()
                    return {
                        "status": "success",
                        "operation": "similarity",
                        "similarity_matrix": matrix,
                        "count": len(texts_list),
                    }

                if not text_b:
                    return {"status": "error", "error": "Provide text_b or texts list in params"}

                # Pairwise similarity benefits from semantic too.
                semantic = _semantic_encode([text_a, text_b])
                if semantic is not None:
                    score = float(np.dot(semantic[0], semantic[1]))
                    return {
                        "status": "success",
                        "operation": "similarity",
                        "backend": _SEMANTIC_BACKEND,
                        "similarity": round(score, 4),
                        "text_a": text_a[:100],
                        "text_b": text_b[:100],
                    }
                vectors = _vectorize([text_a, text_b])
                score = _cosine(vectors[0], vectors[1])
                return {
                    "status": "success",
                    "operation": "similarity",
                    "backend": "tfidf_fallback",
                    "similarity": round(score, 4),
                    "text_a": text_a[:100],
                    "text_b": text_b[:100],
                }

            elif operation == "classify":
                labels = params.get("labels", [])
                if not labels:
                    return {"status": "error", "error": "Provide 'labels' list in params"}

                # Use char-level n-grams so single-word labels still get meaningful overlap
                all_texts = [text] + labels
                vectors = _vectorize(all_texts, char_level=True)
                query_vec = vectors[0]
                label_vecs = vectors[1:]
                scores = {
                    label: round(_cosine(query_vec, label_vecs[i]), 4)
                    for i, label in enumerate(labels)
                }

                # Fallback: if all char-level scores are 0, use word containment
                if all(v == 0 for v in scores.values()):
                    text_lower = text.lower()
                    scores = {
                        label: round(sum(w in text_lower for w in label.lower().split()) / max(len(label.split()), 1), 4)
                        for label in labels
                    }

                top_label = max(scores, key=scores.__getitem__)
                return {
                    "status": "success",
                    "operation": "classify",
                    "label": top_label,
                    "top_label": top_label,
                    "top_score": scores[top_label],
                    "scores": scores,
                }

            else:
                return {"status": "error", "error": f"Unknown operation: {operation}. Use: embed, batch_embed, similarity, classify"}

        except Exception as e:
            return {"status": "error", "error": str(e), "operation": operation}
