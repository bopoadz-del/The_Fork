"""Zvec Block - Real TF-IDF embeddings + cosine similarity via scikit-learn"""

from typing import Any, Dict, List

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.core.universal_base import UniversalBlock


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
                vectors = _vectorize([text])
                vec = vectors[0].tolist()
                return {
                    "status": "success",
                    "operation": "embed",
                    "vector": vec,
                    "dimensions": len(vec),
                    "text": text[:200],
                }

            elif operation == "batch_embed":
                texts = params.get("texts", [text] if text else [])
                if not texts:
                    return {"status": "error", "error": "Provide 'texts' list in params"}
                vectors = _vectorize(texts)
                return {
                    "status": "success",
                    "operation": "batch_embed",
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

                vectors = _vectorize([text_a, text_b])
                score = _cosine(vectors[0], vectors[1])
                return {
                    "status": "success",
                    "operation": "similarity",
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
