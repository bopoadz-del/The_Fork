"""Vector Search Block - In-memory TF-IDF semantic search (no ChromaDB required)"""

import uuid
from typing import Any, Dict, List

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.core.universal_base import UniversalBlock

# Module-level in-memory store: {collection_name: {"docs": [], "ids": [], "metas": []}}
_STORE: Dict[str, Dict] = {}


def _ensure_collection(name: str):
    if name not in _STORE:
        _STORE[name] = {"docs": [], "ids": [], "metas": []}


def _search_collection(collection: str, query: str, n: int) -> List[Dict]:
    col = _STORE.get(collection, {})
    docs = col.get("docs", [])
    if not docs:
        return []

    corpus = docs + [query]
    # Use char-level n-grams for better recall on short phrases
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), max_features=512, sublinear_tf=True)
    try:
        tfidf = vec.fit_transform(corpus).toarray()
    except ValueError:
        return []

    query_vec = tfidf[-1]
    doc_vecs = tfidf[:-1]
    scores = cosine_similarity(query_vec.reshape(1, -1), doc_vecs)[0]

    top_indices = np.argsort(scores)[::-1][:n]
    results = []
    for i in top_indices:
        results.append({
            "id": col["ids"][i],
            "text": docs[i],
            "score": round(float(scores[i]), 4),
            "metadata": col["metas"][i] if col["metas"] else {},
        })
    return results


class VectorSearchBlock(UniversalBlock):
    """In-memory semantic search via TF-IDF cosine similarity"""

    auto_validate = False
    name = "vector_search"
    version = "2.0"
    description = "Semantic search over in-memory document collections — no external DB needed"
    layer = 2
    tags = ["ai", "core", "vector", "search"]
    requires = []

    ui_schema = {
        "input": {
            "type": "text",
            "accept": None,
            "placeholder": "Search knowledge base...",
            "multiline": False,
        },
        "output": {
            "type": "list",
            "fields": [{"name": "results", "type": "array", "label": "Matches"}],
        },
        "quick_actions": [
            {"icon": "🔍", "label": "Search Docs", "prompt": "Search for similar documents about"}
        ],
    }

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        operation = params.get("operation", "search")
        collection = params.get("collection", "default")

        query = ""
        if isinstance(input_data, str):
            query = input_data
        elif isinstance(input_data, dict):
            query = (input_data.get("query") or input_data.get("text") or
                     input_data.get("input") or params.get("query", ""))
        else:
            query = params.get("query", "")

        try:
            if operation == "create_collection":
                name = str(query or collection)
                _ensure_collection(name)
                return {"status": "success", "operation": "create_collection", "collection": name}

            elif operation == "list_collections":
                return {
                    "status": "success",
                    "operation": "list_collections",
                    "collections": list(_STORE.keys()),
                    "count": len(_STORE),
                }

            elif operation == "count":
                _ensure_collection(collection)
                return {
                    "status": "success",
                    "operation": "count",
                    "collection": collection,
                    "count": len(_STORE[collection]["docs"]),
                }

            elif operation == "add":
                _ensure_collection(collection)
                docs = []
                if isinstance(input_data, list):
                    docs = input_data
                elif isinstance(input_data, dict):
                    # InputAdapter wraps lists as {"items": [...]}
                    docs = (input_data.get("documents") or input_data.get("items") or
                            input_data.get("texts") or [])
                    if isinstance(docs, str):
                        docs = [docs]
                    if not docs and input_data.get("text"):
                        docs = [input_data["text"]]
                elif isinstance(input_data, str) and input_data:
                    docs = [input_data]

                if not docs:
                    return {"status": "error", "error": "No documents provided. Pass list or {documents: [...]}"}

                ids = params.get("ids", [str(uuid.uuid4()) for _ in docs])
                metas = params.get("metadatas", [{} for _ in docs])

                col = _STORE[collection]
                col["docs"].extend(docs)
                col["ids"].extend(ids[:len(docs)])
                col["metas"].extend(metas[:len(docs)])

                return {
                    "status": "success",
                    "operation": "add",
                    "collection": collection,
                    "added": len(docs),
                    "total": len(col["docs"]),
                }

            elif operation == "delete_collection":
                if collection in _STORE:
                    del _STORE[collection]
                    return {"status": "success", "operation": "delete_collection", "collection": collection}
                return {"status": "error", "error": f"Collection '{collection}' not found"}

            elif operation == "search":
                if not query:
                    return {"status": "error", "error": "Query text required for search"}
                _ensure_collection(collection)
                n = min(int(params.get("n_results", 5)), 20)
                results = _search_collection(collection, query, n)
                return {
                    "status": "success",
                    "operation": "search",
                    "collection": collection,
                    "query": query,
                    "results": results,
                    "total": len(results),
                }

            else:
                return {"status": "error", "error": f"Unknown operation: {operation}. Use: search, add, create_collection, list_collections, count, delete_collection"}

        except Exception as e:
            return {"status": "error", "error": str(e), "operation": operation}
