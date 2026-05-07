"""Vector Search Block - Vector similarity search using ChromaDB."""

import os
import hashlib
import json
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass
from app.core.block import BaseBlock, BlockConfig

# Optional numpy import
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    np = None
    NUMPY_AVAILABLE = False


@dataclass
class Document:
    """Represents a document in the vector store."""
    id: str
    text: str
    metadata: Dict[str, Any]
    embedding: Optional[List[float]] = None


class VectorSearchBlock(BaseBlock):
    """Vector similarity search using ChromaDB for semantic document retrieval."""
    
    def __init__(self):
        super().__init__(BlockConfig(
            name="vector_search",
            version="1.0",
            description="Vector similarity search using ChromaDB for semantic document retrieval",
            requires_api_key=False,
            supported_inputs=["query", "text", "documents"],
            supported_outputs=["results", "embeddings", "matches"]
        ,
            layer=2,
            tags=["ai", "core", "vector", "search"]))
        self._chromadb_available = self._check_chromadb()
        self._sentence_transformers_available = self._check_sentence_transformers()
        self._openai_available = self._check_openai()
        
        # Default collection name
        self.default_collection = os.getenv("VECTOR_COLLECTION", "default")
        self.persist_directory = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma_db")
        
        # Embedding model cache
        self._embedding_model = None
        self._chroma_client = None
        
        # Ensure persist directory exists
        os.makedirs(self.persist_directory, exist_ok=True)
    
    def _check_chromadb(self) -> bool:
        try:
            import chromadb
            return True
        except ImportError:
            return False
    
    def _check_sentence_transformers(self) -> bool:
        try:
            from sentence_transformers import SentenceTransformer
            return True
        except ImportError:
            return False
    
    def _check_openai(self) -> bool:
        try:
            import openai
            return True
        except ImportError:
            return False
    
    def _get_chroma_client(self):
        """Get or create ChromaDB client."""
        if self._chroma_client:
            return self._chroma_client
        
        if not self._chromadb_available:
            raise RuntimeError("ChromaDB not installed. Install with: pip install chromadb")
        
        import chromadb
        
        self._chroma_client = chromadb.PersistentClient(path=self.persist_directory)
        return self._chroma_client
    
    def _get_embedding_model(self, model_name: Optional[str] = None):
        """Get or load embedding model."""
        if self._embedding_model:
            return self._embedding_model
        
        if not self._sentence_transformers_available:
            raise RuntimeError("sentence-transformers not installed")
        
        from sentence_transformers import SentenceTransformer
        
        model_name = model_name or os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
        self._embedding_model = SentenceTransformer(model_name)
        return self._embedding_model
    
    async def process(self, input_data: Any, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Process vector search operation."""
        params = params or {}
        operation = params.get("operation", "search")
        
        operations = {
            "search": self._search,
            "add": self._add_documents,
            "delete": self._delete_documents,
            "update": self._update_documents,
            "get": self._get_documents,
            "embed": self._embed_text,
            "create_collection": self._create_collection,
            "list_collections": self._list_collections,
            "delete_collection": self._delete_collection,
            "peek": self._peek_collection,
            "count": self._count_documents,
        }
        
        if operation in operations:
            return await operations[operation](input_data, params)
        else:
            return {
                "error": f"Unknown operation: {operation}",
                "available_operations": list(operations.keys()),
                "confidence": 0.0
            }
    
    async def _search(self, input_data: Any, params: Dict) -> Dict:
        """Search for similar documents."""
        query = self._get_query(input_data)
        collection_name = params.get("collection", self.default_collection)
        top_k = params.get("top_k", 5)
        filter_dict = params.get("filter", {})
        embedding_provider = params.get("embedding_provider", "local")
        
        result = {
            "operation": "search",
            "query": query,
            "collection": collection_name,
            "top_k": top_k,
        }
        
        if not self._chromadb_available:
            return self._mock_search_result(query, top_k)
        
        try:
            client = self._get_chroma_client()
            
            try:
                collection = client.get_collection(name=collection_name)
            except Exception:
                return {
                    **result,
                    "error": f"Collection '{collection_name}' not found. Create it first.",
                    "confidence": 0.0
                }
            
            # Get query embedding
            query_embedding = await self._get_embedding(query, embedding_provider)
            
            # Search
            search_results = collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
                where=filter_dict if filter_dict else None,
                include=["metadatas", "documents", "distances"]
            )
            
            # Format results
            matches = []
            for i in range(len(search_results["ids"][0])):
                matches.append({
                    "id": search_results["ids"][0][i],
                    "text": search_results["documents"][0][i] if search_results["documents"] else None,
                    "metadata": search_results["metadatas"][0][i] if search_results["metadatas"] else {},
                    "distance": search_results["distances"][0][i] if search_results["distances"] else None,
                    "score": 1 - (search_results["distances"][0][i] if search_results["distances"] else 0)
                })
            
            result.update({
                "matches": matches,
                "match_count": len(matches),
                "confidence": 0.95 if matches else 0.0
            })
            
        except Exception as e:
            result.update({
                "error": str(e),
                "confidence": 0.0
            })
        
        return result
    
    async def _add_documents(self, input_data: Any, params: Dict) -> Dict:
        """Add documents to vector store."""
        documents = self._get_documents(input_data)
        collection_name = params.get("collection", self.default_collection)
        embedding_provider = params.get("embedding_provider", "local")
        
        result = {
            "operation": "add",
            "collection": collection_name,
            "document_count": len(documents),
        }
        
        if not self._chromadb_available:
            return {
                **result,
                "mock": True,
                "message": "ChromaDB not installed",
                "confidence": 0.5
            }
        
        try:
            client = self._get_chroma_client()
            
            # Get or create collection
            try:
                collection = client.get_collection(name=collection_name)
            except Exception:
                collection = client.create_collection(name=collection_name)
            
            # Prepare data
            ids = []
            texts = []
            metadatas = []
            embeddings = []
            
            for doc in documents:
                doc_id = doc.get("id") or self._generate_id(doc["text"])
                ids.append(doc_id)
                texts.append(doc["text"])
                metadatas.append(doc.get("metadata", {}))
                
                # Generate embedding if provided
                if "embedding" in doc:
                    embeddings.append(doc["embedding"])
            
            # Generate embeddings if not provided
            if not embeddings:
                embeddings = await self._get_embeddings(texts, embedding_provider)
            else:
                # Generate remaining embeddings
                remaining_texts = texts[len(embeddings):]
                if remaining_texts:
                    new_embeddings = await self._get_embeddings(remaining_texts, embedding_provider)
                    embeddings.extend(new_embeddings)
            
            # Add to collection
            collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=texts,
                metadatas=metadatas
            )
            
            result.update({
                "added_count": len(ids),
                "ids": ids,
                "confidence": 1.0
            })
            
        except Exception as e:
            result.update({
                "error": str(e),
                "confidence": 0.0
            })
        
        return result
    
    async def _delete_documents(self, input_data: Any, params: Dict) -> Dict:
        """Delete documents from vector store."""
        collection_name = params.get("collection", self.default_collection)
        ids = params.get("ids", [])
        filter_dict = params.get("filter", {})
        
        result = {
            "operation": "delete",
            "collection": collection_name,
        }
        
        if not self._chromadb_available:
            return {
                **result,
                "mock": True,
                "confidence": 0.5
            }
        
        try:
            client = self._get_chroma_client()
            collection = client.get_collection(name=collection_name)
            
            if ids:
                collection.delete(ids=ids)
                result["deleted_ids"] = ids
            elif filter_dict:
                collection.delete(where=filter_dict)
                result["deleted_filter"] = filter_dict
            else:
                return {
                    **result,
                    "error": "Provide ids or filter to delete",
                    "confidence": 0.0
                }
            
            result.update({
                "deleted": True,
                "confidence": 1.0
            })
            
        except Exception as e:
            result.update({
                "error": str(e),
                "confidence": 0.0
            })
        
        return result
    
    async def _update_documents(self, input_data: Any, params: Dict) -> Dict:
        """Update documents in vector store."""
        documents = self._get_documents(input_data)
        collection_name = params.get("collection", self.default_collection)
        embedding_provider = params.get("embedding_provider", "local")
        
        result = {
            "operation": "update",
            "collection": collection_name,
        }
        
        if not self._chromadb_available:
            return {
                **result,
                "mock": True,
                "confidence": 0.5
            }
        
        try:
            client = self._get_chroma_client()
            collection = client.get_collection(name=collection_name)
            
            for doc in documents:
                doc_id = doc.get("id")
                if not doc_id:
                    continue
                
                update_data = {}
                if "text" in doc:
                    update_data["documents"] = doc["text"]
                    # Regenerate embedding
                    update_data["embeddings"] = await self._get_embedding(
                        doc["text"], embedding_provider
                    )
                if "metadata" in doc:
                    update_data["metadatas"] = doc["metadata"]
                
                if update_data:
                    collection.update(
                        ids=[doc_id],
                        **update_data
                    )
            
            result.update({
                "updated_count": len(documents),
                "confidence": 1.0
            })
            
        except Exception as e:
            result.update({
                "error": str(e),
                "confidence": 0.0
            })
        
        return result
    
    async def _get_documents(self, input_data: Any, params: Dict) -> Dict:
        """Get documents by ID."""
        collection_name = params.get("collection", self.default_collection)
        ids = params.get("ids", [])
        
        if isinstance(input_data, list):
            ids = input_data
        elif isinstance(input_data, dict):
            ids = input_data.get("ids", [])
        
        result = {
            "operation": "get",
            "collection": collection_name,
            "requested_ids": ids,
        }
        
        if not self._chromadb_available:
            return {
                **result,
                "mock": True,
                "confidence": 0.5
            }
        
        try:
            client = self._get_chroma_client()
            collection = client.get_collection(name=collection_name)
            
            docs = collection.get(
                ids=ids,
                include=["metadatas", "documents", "embeddings"]
            )
            
            documents = []
            for i in range(len(docs["ids"])):
                documents.append({
                    "id": docs["ids"][i],
                    "text": docs["documents"][i] if docs["documents"] else None,
                    "metadata": docs["metadatas"][i] if docs["metadatas"] else {},
                    "embedding": docs["embeddings"][i] if docs["embeddings"] else None,
                })
            
            result.update({
                "documents": documents,
                "count": len(documents),
                "confidence": 1.0
            })
            
        except Exception as e:
            result.update({
                "error": str(e),
                "confidence": 0.0
            })
        
        return result
    
    async def _embed_text(self, input_data: Any, params: Dict) -> Dict:
        """Generate embeddings for text."""
        texts = self._get_texts(input_data)
        embedding_provider = params.get("embedding_provider", "local")
        
        result = {
            "operation": "embed",
            "text_count": len(texts),
        }
        
        try:
            embeddings = await self._get_embeddings(texts, embedding_provider)
            
            result.update({
                "embeddings": embeddings,
                "dimensions": len(embeddings[0]) if embeddings else 0,
                "provider": embedding_provider,
                "confidence": 1.0
            })
            
        except Exception as e:
            result.update({
                "error": str(e),
                "confidence": 0.0
            })
        
        return result
    
    async def _create_collection(self, input_data: Any, params: Dict) -> Dict:
        """Create a new collection."""
        collection_name = params.get("collection") or input_data
        
        result = {
            "operation": "create_collection",
            "collection": collection_name,
        }
        
        if not self._chromadb_available:
            return {
                **result,
                "mock": True,
                "confidence": 0.5
            }
        
        try:
            client = self._get_chroma_client()
            collection = client.create_collection(name=collection_name)
            
            result.update({
                "created": True,
                "confidence": 1.0
            })
            
        except Exception as e:
            result.update({
                "error": str(e),
                "confidence": 0.0
            })
        
        return result
    
    async def _list_collections(self, input_data: Any, params: Dict) -> Dict:
        """List all collections."""
        result = {
            "operation": "list_collections",
        }
        
        if not self._chromadb_available:
            return {
                **result,
                "collections": ["default"],
                "mock": True,
                "confidence": 0.5
            }
        
        try:
            client = self._get_chroma_client()
            collections = client.list_collections()
            
            result.update({
                "collections": [c.name for c in collections],
                "count": len(collections),
                "confidence": 1.0
            })
            
        except Exception as e:
            result.update({
                "error": str(e),
                "confidence": 0.0
            })
        
        return result
    
    async def _delete_collection(self, input_data: Any, params: Dict) -> Dict:
        """Delete a collection."""
        collection_name = params.get("collection") or input_data
        
        result = {
            "operation": "delete_collection",
            "collection": collection_name,
        }
        
        if not self._chromadb_available:
            return {
                **result,
                "mock": True,
                "confidence": 0.5
            }
        
        try:
            client = self._get_chroma_client()
            client.delete_collection(name=collection_name)
            
            result.update({
                "deleted": True,
                "confidence": 1.0
            })
            
        except Exception as e:
            result.update({
                "error": str(e),
                "confidence": 0.0
            })
        
        return result
    
    async def _peek_collection(self, input_data: Any, params: Dict) -> Dict:
        """Peek at documents in a collection."""
        collection_name = params.get("collection", self.default_collection)
        limit = params.get("limit", 10)
        
        result = {
            "operation": "peek",
            "collection": collection_name,
            "limit": limit,
        }
        
        if not self._chromadb_available:
            return {
                **result,
                "mock": True,
                "confidence": 0.5
            }
        
        try:
            client = self._get_chroma_client()
            collection = client.get_collection(name=collection_name)
            
            peek = collection.peek(limit=limit)
            
            documents = []
            for i in range(len(peek["ids"])):
                documents.append({
                    "id": peek["ids"][i],
                    "text": peek["documents"][i] if peek["documents"] else None,
                    "metadata": peek["metadatas"][i] if peek["metadatas"] else {},
                })
            
            result.update({
                "documents": documents,
                "count": len(documents),
                "confidence": 1.0
            })
            
        except Exception as e:
            result.update({
                "error": str(e),
                "confidence": 0.0
            })
        
        return result
    
    async def _count_documents(self, input_data: Any, params: Dict) -> Dict:
        """Count documents in a collection."""
        collection_name = params.get("collection", self.default_collection)
        
        result = {
            "operation": "count",
            "collection": collection_name,
        }
        
        if not self._chromadb_available:
            return {
                **result,
                "count": 0,
                "mock": True,
                "confidence": 0.5
            }
        
        try:
            client = self._get_chroma_client()
            collection = client.get_collection(name=collection_name)
            count = collection.count()
            
            result.update({
                "count": count,
                "confidence": 1.0
            })
            
        except Exception as e:
            result.update({
                "error": str(e),
                "confidence": 0.0
            })
        
        return result
    
    async def _get_embedding(self, text: str, provider: str = "local") -> List[float]:
        """Get embedding for a single text."""
        embeddings = await self._get_embeddings([text], provider)
        return embeddings[0]
    
    async def _get_embeddings(self, texts: List[str], provider: str = "local") -> List[List[float]]:
        """Get embeddings for multiple texts."""
        if provider == "local" and self._sentence_transformers_available:
            model = self._get_embedding_model()
            embeddings = model.encode(texts)
            return embeddings.tolist()
        
        elif provider == "openai" and self._openai_available:
            import openai
            client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            
            response = await client.embeddings.create(
                model="text-embedding-ada-002",
                input=texts
            )
            return [item.embedding for item in response.data]
        
        else:
            # Mock embeddings (random for testing)
            import random
            return [[random.random() for _ in range(384)] for _ in texts]
    
    def _get_query(self, input_data: Any) -> str:
        """Extract query from input."""
        if isinstance(input_data, str):
            return input_data
        if isinstance(input_data, dict):
            if "query" in input_data:
                return input_data["query"]
            if "text" in input_data:
                return input_data["text"]
        raise ValueError("Invalid query input")
    
    def _get_documents(self, input_data: Any) -> List[Dict]:
        """Extract documents from input."""
        if isinstance(input_data, list):
            return input_data
        if isinstance(input_data, dict):
            if "documents" in input_data:
                return input_data["documents"]
            if "text" in input_data:
                return [{"text": input_data["text"], "metadata": input_data.get("metadata", {})}]
        raise ValueError("Invalid documents input")
    
    def _get_texts(self, input_data: Any) -> List[str]:
        """Extract texts from input."""
        if isinstance(input_data, str):
            return [input_data]
        if isinstance(input_data, list):
            return input_data
        if isinstance(input_data, dict):
            if "texts" in input_data:
                return input_data["texts"]
            if "text" in input_data:
                return [input_data["text"]]
        raise ValueError("Invalid texts input")
    
    def _generate_id(self, text: str) -> str:
        """Generate ID from text."""
        return hashlib.md5(text.encode()).hexdigest()[:16]
    
    def _mock_search_result(self, query: str, top_k: int) -> Dict:
        """Return mock search result."""
        return {
            "operation": "search",
            "query": query,
            "mock": True,
            "matches": [
                {
                    "id": f"mock_{i}",
                    "text": f"Mock result {i} for query: {query}",
                    "metadata": {"source": "mock"},
                    "distance": 0.1 * (i + 1),
                    "score": 1.0 - (0.1 * (i + 1))
                }
                for i in range(min(top_k, 3))
            ],
            "match_count": min(top_k, 3),
            "message": "ChromaDB not installed. Install with: pip install chromadb sentence-transformers",
            "confidence": 0.5
        }
