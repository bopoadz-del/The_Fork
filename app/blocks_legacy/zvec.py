"""Zvec Block - Zero-shot vector embeddings and semantic operations."""

import os
import hashlib
import random
from typing import Any, Dict, List, Optional
from app.core.block import BaseBlock, BlockConfig


class ZvecBlock(BaseBlock):
    """Zero-shot vector embeddings and semantic vector operations.
    
    Provides zero-shot classification, semantic similarity, and vector arithmetic
    without requiring fine-tuning or labeled training data.
    """

    def __init__(self):
        super().__init__(BlockConfig(
            name="zvec",
            version="1.0",
            description="Zero-shot vector embeddings and semantic operations - classify, compare, and manipulate vectors without training",
            requires_api_key=False,
            supported_inputs=["text", "query", "vectors", "labels"],
            supported_outputs=["embeddings", "similarities", "classifications", "vectors"]
        ,
            layer=2,
            tags=["ai", "vector", "zero-shot"]))
        self._sentence_transformers_available = self._check_sentence_transformers()
        self._numpy_available = self._check_numpy()
        self._embedding_model = None

    def _check_sentence_transformers(self) -> bool:
        try:
            from sentence_transformers import SentenceTransformer
            return True
        except ImportError:
            return False

    def _check_numpy(self) -> bool:
        try:
            import numpy as np
            return True
        except ImportError:
            return False

    def _get_embedding_model(self, model_name: Optional[str] = None):
        """Get or load embedding model."""
        if self._embedding_model:
            return self._embedding_model

        if not self._sentence_transformers_available:
            raise RuntimeError("sentence-transformers not installed")

        from sentence_transformers import SentenceTransformer

        model_name = model_name or os.getenv("ZVEC_MODEL", "all-MiniLM-L6-v2")
        self._embedding_model = SentenceTransformer(model_name)
        return self._embedding_model

    async def process(self, input_data: Any, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Main processing logic for Zvec operations."""
        params = params or {}
        operation = params.get("operation", "embed")

        operations = {
            "embed": self._embed,
            "classify": self._zero_shot_classify,
            "similarity": self._compute_similarity,
            "search": self._semantic_search,
            "cluster": self._cluster,
            "analogy": self._vector_analogy,
        }

        if operation in operations:
            return await operations[operation](input_data, params)
        else:
            return {
                "error": f"Unknown operation: {operation}",
                "available_operations": list(operations.keys()),
                "confidence": 0.0
            }

    async def _embed(self, input_data: Any, params: Dict) -> Dict:
        """Generate embeddings for text(s)."""
        texts = self._get_texts(input_data)
        model_name = params.get("model", "all-MiniLM-L6-v2")

        result = {
            "operation": "embed",
            "text_count": len(texts),
            "model": model_name,
        }

        if not self._sentence_transformers_available:
            # Return mock embeddings for testing
            embeddings = [[random.random() for _ in range(384)] for _ in texts]
            result.update({
                "embeddings": embeddings,
                "dimensions": 384,
                "mock": True,
                "confidence": 0.5
            })
            return result

        try:
            model = self._get_embedding_model(model_name)
            embeddings = model.encode(texts)
            embeddings_list = embeddings.tolist() if hasattr(embeddings, 'tolist') else embeddings

            result.update({
                "embeddings": embeddings_list,
                "dimensions": len(embeddings_list[0]) if embeddings_list else 0,
                "mock": False,
                "confidence": 0.98
            })
        except Exception as e:
            result.update({
                "error": str(e),
                "confidence": 0.0
            })

        return result

    async def _zero_shot_classify(self, input_data: Any, params: Dict) -> Dict:
        """Zero-shot classification using embeddings."""
        text = self._get_text(input_data)
        candidate_labels = params.get("labels", params.get("candidate_labels", []))
        multi_label = params.get("multi_label", False)

        result = {
            "operation": "classify",
            "text": text[:200] + "..." if len(text) > 200 else text,
            "candidate_labels": candidate_labels,
        }

        if not candidate_labels:
            return {
                **result,
                "error": "No candidate_labels provided",
                "confidence": 0.0
            }

        if not self._sentence_transformers_available or not self._numpy_available:
            # Mock classification
            scores = [random.random() for _ in candidate_labels]
            total = sum(scores)
            probabilities = [s / total for s in scores]
            
            label_scores = list(zip(candidate_labels, probabilities))
            label_scores.sort(key=lambda x: x[1], reverse=True)

            result.update({
                "labels": [l for l, _ in label_scores],
                "scores": [s for _, s in label_scores],
                "top_label": label_scores[0][0],
                "top_score": label_scores[0][1],
                "mock": True,
                "confidence": 0.5
            })
            return result

        try:
            import numpy as np

            # Encode text and labels
            model = self._get_embedding_model()
            text_embedding = model.encode([text])[0]
            label_embeddings = model.encode(candidate_labels)

            # Compute similarities
            similarities = []
            for label_emb in label_embeddings:
                similarity = np.dot(text_embedding, label_emb) / (
                    np.linalg.norm(text_embedding) * np.linalg.norm(label_emb)
                )
                similarities.append(float(similarity))

            # Convert to probabilities
            if multi_label:
                # Sigmoid for multi-label
                probs = [1 / (1 + np.exp(-s * 10)) for s in similarities]
            else:
                # Softmax for single-label
                exp_scores = [np.exp(s) for s in similarities]
                total = sum(exp_scores)
                probs = [s / total for s in exp_scores]

            label_scores = list(zip(candidate_labels, probs))
            label_scores.sort(key=lambda x: x[1], reverse=True)

            result.update({
                "labels": [l for l, _ in label_scores],
                "scores": [round(s, 4) for _, s in label_scores],
                "top_label": label_scores[0][0],
                "top_score": round(label_scores[0][1], 4),
                "mock": False,
                "confidence": 0.92
            })

        except Exception as e:
            result.update({
                "error": str(e),
                "confidence": 0.0
            })

        return result

    async def _compute_similarity(self, input_data: Any, params: Dict) -> Dict:
        """Compute similarity between texts."""
        texts = self._get_texts(input_data)
        pairs = params.get("pairs")  # Optional specific pairs to compare

        result = {
            "operation": "similarity",
            "text_count": len(texts),
        }

        if len(texts) < 2 and not pairs:
            return {
                **result,
                "error": "Need at least 2 texts to compare",
                "confidence": 0.0
            }

        if not self._sentence_transformers_available or not self._numpy_available:
            # Return mock similarities
            n = len(texts)
            sim_matrix = [[random.random() for _ in range(n)] for _ in range(n)]
            result.update({
                "similarity_matrix": sim_matrix,
                "mock": True,
                "confidence": 0.5
            })
            return result

        try:
            import numpy as np

            model = self._get_embedding_model()
            embeddings = model.encode(texts)

            if pairs:
                # Compute specific pairs
                similarities = []
                for i, j in pairs:
                    if i < len(texts) and j < len(texts):
                        emb_i = embeddings[i]
                        emb_j = embeddings[j]
                        similarity = np.dot(emb_i, emb_j) / (
                            np.linalg.norm(emb_i) * np.linalg.norm(emb_j)
                        )
                        similarities.append({
                            "pair": (i, j),
                            "text_i": texts[i][:50],
                            "text_j": texts[j][:50],
                            "similarity": round(float(similarity), 4)
                        })
                result["pairwise_similarities"] = similarities
            else:
                # Compute full similarity matrix
                n = len(texts)
                sim_matrix = []
                for i in range(n):
                    row = []
                    for j in range(n):
                        similarity = np.dot(embeddings[i], embeddings[j]) / (
                            np.linalg.norm(embeddings[i]) * np.linalg.norm(embeddings[j])
                        )
                        row.append(round(float(similarity), 4))
                    sim_matrix.append(row)
                result["similarity_matrix"] = sim_matrix

            result.update({
                "mock": False,
                "confidence": 0.95
            })

        except Exception as e:
            result.update({
                "error": str(e),
                "confidence": 0.0
            })

        return result

    async def _semantic_search(self, input_data: Any, params: Dict) -> Dict:
        """Semantic search across corpus."""
        query = self._get_text(input_data)
        corpus = params.get("corpus", params.get("documents", []))
        top_k = params.get("top_k", 5)

        result = {
            "operation": "search",
            "query": query[:200] + "..." if len(query) > 200 else query,
            "corpus_size": len(corpus),
        }

        if not corpus:
            return {
                **result,
                "error": "No corpus provided",
                "confidence": 0.0
            }

        if not self._sentence_transformers_available or not self._numpy_available:
            # Mock search results
            matches = [
                {
                    "text": corpus[i % len(corpus)],
                    "score": round(random.random(), 4),
                    "index": i
                }
                for i in range(min(top_k, len(corpus)))
            ]
            matches.sort(key=lambda x: x["score"], reverse=True)
            result.update({
                "matches": matches,
                "mock": True,
                "confidence": 0.5
            })
            return result

        try:
            import numpy as np

            model = self._get_embedding_model()
            query_emb = model.encode([query])[0]
            corpus_emb = model.encode(corpus)

            # Compute similarities
            similarities = []
            for i, doc_emb in enumerate(corpus_emb):
                similarity = np.dot(query_emb, doc_emb) / (
                    np.linalg.norm(query_emb) * np.linalg.norm(doc_emb)
                )
                similarities.append((i, float(similarity)))

            # Sort by similarity
            similarities.sort(key=lambda x: x[1], reverse=True)

            matches = [
                {
                    "text": corpus[idx][:500] if len(corpus[idx]) > 500 else corpus[idx],
                    "score": round(score, 4),
                    "index": idx
                }
                for idx, score in similarities[:top_k]
            ]

            result.update({
                "matches": matches,
                "top_score": matches[0]["score"] if matches else 0,
                "mock": False,
                "confidence": 0.94
            })

        except Exception as e:
            result.update({
                "error": str(e),
                "confidence": 0.0
            })

        return result

    async def _cluster(self, input_data: Any, params: Dict) -> Dict:
        """Simple clustering of texts."""
        texts = self._get_texts(input_data)
        n_clusters = params.get("n_clusters", 3)

        result = {
            "operation": "cluster",
            "text_count": len(texts),
            "n_clusters": n_clusters,
        }

        if len(texts) < n_clusters:
            return {
                **result,
                "error": f"Need at least {n_clusters} texts for clustering",
                "confidence": 0.0
            }

        if not self._sentence_transformers_available or not self._numpy_available:
            # Mock clustering
            clusters = {i: [] for i in range(n_clusters)}
            for i, text in enumerate(texts):
                cluster_id = i % n_clusters
                clusters[cluster_id].append({"text": text[:100], "index": i})

            result.update({
                "clusters": clusters,
                "mock": True,
                "confidence": 0.5
            })
            return result

        try:
            from sklearn.cluster import KMeans

            model = self._get_embedding_model()
            embeddings = model.encode(texts)

            # Simple K-means clustering
            kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
            labels = kmeans.fit_predict(embeddings)

            clusters = {i: [] for i in range(n_clusters)}
            for i, (text, label) in enumerate(zip(texts, labels)):
                clusters[int(label)].append({
                    "text": text[:200] if len(text) > 200 else text,
                    "index": i
                })

            result.update({
                "clusters": clusters,
                "cluster_centers": kmeans.cluster_centers_.tolist() if hasattr(kmeans.cluster_centers_, 'tolist') else [],
                "mock": False,
                "confidence": 0.88
            })

        except Exception as e:
            # Fallback without sklearn
            clusters = {i: [] for i in range(n_clusters)}
            for i, text in enumerate(texts):
                cluster_id = i % n_clusters
                clusters[cluster_id].append({"text": text[:100], "index": i})

            result.update({
                "clusters": clusters,
                "error": str(e),
                "mock": True,
                "confidence": 0.4
            })

        return result

    async def _vector_analogy(self, input_data: Any, params: Dict) -> Dict:
        """Vector arithmetic: king - man + woman = queen."""
        a = params.get("a")  # king
        b = params.get("b")  # man
        c = params.get("c")  # woman

        result = {
            "operation": "analogy",
            "expression": f"{a} - {b} + {c}",
        }

        if not all([a, b, c]):
            return {
                **result,
                "error": "Need three words: a, b, c (for a - b + c)",
                "confidence": 0.0
            }

        if not self._sentence_transformers_available or not self._numpy_available:
            result.update({
                "result": f"{c}_{a}",  # Mock result
                "mock": True,
                "confidence": 0.5
            })
            return result

        try:
            import numpy as np

            model = self._get_embedding_model()
            words = [a, b, c]
            embeddings = model.encode(words)

            # Vector arithmetic: a - b + c
            result_vector = embeddings[0] - embeddings[1] + embeddings[2]

            # Find closest word (would need vocab, return vector for now)
            result.update({
                "result_vector": result_vector.tolist()[:10] + ["..."],  # Truncated
                "expression": f"{a} - {b} + {c}",
                "mock": False,
                "confidence": 0.85
            })

        except Exception as e:
            result.update({
                "error": str(e),
                "confidence": 0.0
            })

        return result

    # Helper methods

    def _get_text(self, input_data: Any) -> str:
        """Extract single text from input."""
        if isinstance(input_data, str):
            return input_data
        if isinstance(input_data, dict):
            if "text" in input_data:
                return input_data["text"]
            if "query" in input_data:
                return input_data["query"]
            if "result" in input_data and isinstance(input_data["result"], dict):
                return input_data["result"].get("text", "")
        raise ValueError("Invalid text input")

    def _get_texts(self, input_data: Any) -> List[str]:
        """Extract list of texts from input."""
        if isinstance(input_data, list):
            return [str(item) for item in input_data]
        if isinstance(input_data, dict):
            if "texts" in input_data:
                return input_data["texts"]
            if "documents" in input_data:
                return input_data["documents"]
            if "corpus" in input_data:
                return input_data["corpus"]
            if "text" in input_data:
                return [input_data["text"]]
        if isinstance(input_data, str):
            return [input_data]
        raise ValueError("Invalid texts input")
