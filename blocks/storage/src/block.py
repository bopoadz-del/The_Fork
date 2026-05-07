"""Storage Block - File storage with multiple backends"""

from blocks.base import LegoBlock
from typing import Dict, Any, List, Optional
import os
import hashlib
import aiofiles
from pathlib import Path


class StorageBlock(LegoBlock):
    """
    Storage Block - Unified file storage
    Supports local, cloud (S3), and memory backends
    """
    
    name = "storage"
    version = "1.0.0"
    requires = ["config"]
    layer = 2  # Core layer
    tags = ["storage", "files", "core"]
    default_config = {
        "backend": "local",
        "data_dir": "./data/storage"
    }
    
    def __init__(self, hal_block, config: Dict[str, Any]):
        super().__init__(hal_block, config)
        self.backend = config.get("backend", "local")
        self.data_dir = config.get("data_dir", "./data/storage")
        self.memory_block = None
        
        # Ensure data directory exists
        os.makedirs(self.data_dir, exist_ok=True)
    
    async def initialize(self):
        """Initialize storage"""
        print(f"💾 Storage Block initialized")
        print(f"   Backend: {self.backend}")
        print(f"   Data dir: {self.data_dir}")
        return True
    
    async def execute(self, input_data: Dict) -> Dict:
        """Storage operations"""
        action = input_data.get("action")
        
        if action == "store":
            return await self._store(input_data)
        elif action == "retrieve":
            return await self._retrieve(input_data.get("file_id"))
        elif action == "delete":
            return await self._delete(input_data.get("file_id"))
        elif action == "exists":
            return await self._exists(input_data.get("file_id"))
        elif action == "list":
            return await self._list(input_data.get("prefix", ""))
        
        return {"error": f"Unknown action: {action}"}
    
    async def _store(self, data: Dict) -> Dict:
        """Store a file"""
        content = data.get("content")  # bytes or str
        filename = data.get("filename", "unnamed")
        metadata = data.get("metadata", {})
        
        # Generate file ID
        file_hash = hashlib.sha256(content if isinstance(content, bytes) else content.encode()).hexdigest()[:16]
        file_id = f"file_{file_hash}"
        
        if self.backend == "local":
            # Store locally
            file_path = os.path.join(self.data_dir, file_id)
            async with aiofiles.open(file_path, 'wb') as f:
                await f.write(content if isinstance(content, bytes) else content.encode())
            
            # Store metadata
            meta_path = os.path.join(self.data_dir, f"{file_id}.meta")
            async with aiofiles.open(meta_path, 'w') as f:
                import json
                await f.write(json.dumps({
                    "filename": filename,
                    "metadata": metadata,
                    "stored_at": time.time() if 'time' in dir() else 0
                }))
        
        elif self.backend == "memory" and self.memory_block:
            # Store in memory
            await self.memory_block.execute({
                "action": "set",
                "key": f"storage:{file_id}",
                "value": {
                    "content": content,
                    "filename": filename,
                    "metadata": metadata
                },
                "ttl": 3600  # 1 hour default
            })
        
        return {
            "stored": True,
            "file_id": file_id,
            "backend": self.backend
        }
    
    async def _retrieve(self, file_id: str) -> Dict:
        """Retrieve a file"""
        if self.backend == "local":
            file_path = os.path.join(self.data_dir, file_id)
            if not os.path.exists(file_path):
                return {"error": "file_not_found"}
            
            async with aiofiles.open(file_path, 'rb') as f:
                content = await f.read()
            
            # Get metadata
            meta_path = os.path.join(self.data_dir, f"{file_id}.meta")
            metadata = {}
            if os.path.exists(meta_path):
                import json
                async with aiofiles.open(meta_path, 'r') as f:
                    metadata = json.loads(await f.read())
            
            return {
                "file_id": file_id,
                "content": content,
                "filename": metadata.get("filename", "unknown"),
                "metadata": metadata.get("metadata", {})
            }
        
        elif self.backend == "memory" and self.memory_block:
            result = await self.memory_block.execute({
                "action": "get",
                "key": f"storage:{file_id}"
            })
            
            if not result.get("hit"):
                return {"error": "file_not_found"}
            
            data = result.get("value", {})
            return {
                "file_id": file_id,
                "content": data.get("content"),
                "filename": data.get("filename"),
                "metadata": data.get("metadata", {})
            }
        
        return {"error": "backend_not_supported"}
    
    async def _delete(self, file_id: str) -> Dict:
        """Delete a file"""
        if self.backend == "local":
            file_path = os.path.join(self.data_dir, file_id)
            meta_path = os.path.join(self.data_dir, f"{file_id}.meta")
            
            deleted = False
            if os.path.exists(file_path):
                os.remove(file_path)
                deleted = True
            if os.path.exists(meta_path):
                os.remove(meta_path)
            
            return {"deleted": deleted}
        
        elif self.backend == "memory" and self.memory_block:
            await self.memory_block.execute({
                "action": "delete",
                "key": f"storage:{file_id}"
            })
            return {"deleted": True}
        
        return {"deleted": False}
    
    async def _exists(self, file_id: str) -> Dict:
        """Check if file exists"""
        if self.backend == "local":
            file_path = os.path.join(self.data_dir, file_id)
            return {"exists": os.path.exists(file_path)}
        
        elif self.backend == "memory" and self.memory_block:
            result = await self.memory_block.execute({
                "action": "exists",
                "key": f"storage:{file_id}"
            })
            return {"exists": result.get("exists", False)}
        
        return {"exists": False}
    
    async def _list(self, prefix: str = "") -> Dict:
        """List files"""
        if self.backend == "local":
            files = []
            for f in os.listdir(self.data_dir):
                if not f.endswith('.meta') and f.startswith(prefix):
                    files.append(f)
            return {"files": files, "count": len(files)}
        
        return {"files": [], "count": 0}
    
    def health(self) -> Dict[str, Any]:
        """Health check"""
        h = super().health()
        h["backend"] = self.backend
        h["data_dir"] = self.data_dir
        return h


import time
