"""Vector Block - Semantic search with embeddings"""

from blocks.base import LegoBlock
from typing import Dict, Any, List, Optional
import hashlib
import numpy as np


class VectorBlock(LegoBlock):
    """
    Vector Block - Semantic search and embeddings
    Supports ChromaDB (cloud) or in-memory (edge)
    """
    
    name = "vector"
    version = "1.0.0"
    requires = ["config", "memory"]
    layer = 3  # Core infrastructure
    tags = ["ai", "vector", "search", "core"]
    default_config = {
        "backend": "chroma",
        "embedding_model": "all-MiniLM-L6-v2",
        "collection": "default",
        "persist_directory": "./data/vector"
    }
    
    def __init__(self, hal_block, config: Dict[str, Any]):
        super().__init__(hal_block, config)
        self.backend = config.get("backend", "chroma")
        self.collection = config.get("collection", "default")
        self.dimension = config.get("dimension", 384)
        self.embedding_model = config.get("embedding_model", "all-MiniLM-L6-v2")
        self.memory_block = None
        
        # In-memory store
        self._vectors = {}  # id -> vector
        self._documents = {}  # id -> document
        self._embeddings_func = None
    
    async def initialize(self):
        """Initialize vector store"""
        print(f"🔍 Vector Block initialized")
        print(f"   Backend: {self.backend}")
        print(f"   Collection: {self.collection}")
        print(f"   Dimension: {self.dimension}")
        
        # Try to load embedding model
        try:
            from sentence_transformers import SentenceTransformer
            self._embeddings_func = SentenceTransformer(self.embedding_model)
            print(f"   Model loaded: {self.embedding_model}")
        except Exception as e:
            print(f"   Using dummy embeddings ({str(e)[:30]}...)")
            self._embeddings_func = None
        
        return True
    
    def _embed(self, text: str) -> List[float]:
        """Generate embedding for text"""
        if self._embeddings_func:
            return self._embeddings_func.encode(text).tolist()
        
        # Dummy embedding for testing
        # Use hash to make it deterministic
        hash_val = hashlib.md5(text.encode()).hexdigest()
        vec = [int(hash_val[i:i+2], 16) / 255.0 for i in range(0, min(len(hash_val), self.dimension * 2), 2)]
        # Pad to dimension
        vec = vec + [0.0] * (self.dimension - len(vec))
        return vec[:self.dimension]
    
    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """Calculate cosine similarity"""
        a = np.array(a)
        b = np.array(b)
        return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))
    
    async def execute(self, input_data: Dict) -> Dict:
        """Vector operations"""
        action = input_data.get("action")
        
        if action == "add":
            return await self._add(input_data)
        elif action == "search":
            return await self._search(input_data)
        elif action == "delete":
            return await self._delete(input_data.get("id"))
        elif action == "get":
            return await self._get(input_data.get("id"))
        elif action == "count":
            return await self._count()
        
        return {"error": f"Unknown action: {action}"}
    
    async def _add(self, data: Dict) -> Dict:
        """Add document to vector store"""
        doc_id = data.get("id") or f"doc_{hashlib.sha256(data.get('text', '').encode()).hexdigest()[:12]}"
        text = data.get("text", "")
        metadata = data.get("metadata", {})
        
        # Generate embedding
        vector = self._embed(text)
        
        if self.backend == "chroma":
            # Would use ChromaDB
            self._vectors[doc_id] = vector
            self._documents[doc_id] = {"text": text, "metadata": metadata}
        
        elif self.backend == "memory" and self.memory_block:
            # Store in memory block
            await self.memory_block.execute({
                "action": "set",
                "key": f"vector:{self.collection}:{doc_id}",
                "value": {
                    "vector": vector,
                    "text": text,
                    "metadata": metadata
                },
                "ttl": 0  # No expiry
            })
        
        else:
            # In-memory fallback
            self._vectors[doc_id] = vector
            self._documents[doc_id] = {"text": text, "metadata": metadata}
        
        return {"added": True, "id": doc_id, "vector_dim": len(vector)}
    
    async def _search(self, data: Dict) -> Dict:
        """Semantic search"""
        query = data.get("query", "")
        top_k = data.get("top_k", 5)
        threshold = data.get("threshold", 0.5)
        
        # Embed query
        query_vec = self._embed(query)
        
        results = []
        
        if self.backend == "memory" and self.memory_block:
            # Get all vectors from memory (inefficient but works for small sets)
            keys_result = await self.memory_block.execute({"action": "keys"})
            for key in keys_result.get("keys", []):
                if key.startswith(f"vector:{self.collection}:"):
                    doc_data = await self.memory_block.execute({"action": "get", "key": key})
                    if doc_data.get("hit"):
                        doc = doc_data.get("value", {})
                        vec = doc.get("vector", [])
                        if vec:
                            score = self._cosine_similarity(query_vec, vec)
                            if score >= threshold:
                                doc_id = key.split(":")[-1]
                                results.append({
                                    "id": doc_id,
                                    "score": float(score),
                                    "text": doc.get("text", "")[:200] + "...",
                                    "metadata": doc.get("metadata", {})
                                })
        else:
            # In-memory search
            for doc_id, vec in self._vectors.items():
                score = self._cosine_similarity(query_vec, vec)
                if score >= threshold:
                    doc = self._documents.get(doc_id, {})
                    results.append({
                        "id": doc_id,
                        "score": float(score),
                        "text": doc.get("text", "")[:200] + "...",
                        "metadata": doc.get("metadata", {})
                    })
        
        # Sort by score
        results.sort(key=lambda x: x["score"], reverse=True)
        
        return {
            "query": query,
            "results": results[:top_k],
            "total_found": len(results)
        }
    
    async def _delete(self, doc_id: str) -> Dict:
        """Delete document"""
        if self.backend == "memory" and self.memory_block:
            await self.memory_block.execute({
                "action": "delete",
                "key": f"vector:{self.collection}:{doc_id}"
            })
        
        if doc_id in self._vectors:
            del self._vectors[doc_id]
        if doc_id in self._documents:
            del self._documents[doc_id]
        
        return {"deleted": True, "id": doc_id}
    
    async def _get(self, doc_id: str) -> Dict:
        """Get document by ID"""
        if self.backend == "memory" and self.memory_block:
            result = await self.memory_block.execute({
                "action": "get",
                "key": f"vector:{self.collection}:{doc_id}"
            })
            if result.get("hit"):
                doc = result.get("value", {})
                return {
                    "id": doc_id,
                    "text": doc.get("text"),
                    "metadata": doc.get("metadata", {})
                }
        
        if doc_id in self._documents:
            return {"id": doc_id, **self._documents[doc_id]}
        
        return {"error": "not_found"}
    
    async def _count(self) -> Dict:
        """Count documents"""
        count = len(self._vectors)
        return {"count": count, "collection": self.collection}
    
    def health(self) -> Dict[str, Any]:
        """Health check"""
        h = super().health()
        h["backend"] = self.backend
        h["collection"] = self.collection
        h["documents"] = len(self._vectors)
        h["embedding_model"] = self.embedding_model
        return h
