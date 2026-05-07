"""Local Drive Block - Local filesystem operations."""

import os
import shutil
import hashlib
from typing import Any, Dict, List, Optional
from pathlib import Path
from app.core.block import BaseBlock, BlockConfig


class LocalDriveBlock(BaseBlock):
    """Local filesystem operations for file management."""
    
    def __init__(self):
        super().__init__(BlockConfig(
            name="local_drive",
            version="1.0",
            description="Local filesystem file upload, download, and management",
            supported_inputs=["file", "file_path"],
            supported_outputs=["file_path", "url", "metadata"]
        ,
            layer=4,
            tags=["integration", "storage", "local"]))
        self.base_path = os.getenv("LOCAL_DRIVE_PATH", "./data")
        os.makedirs(self.base_path, exist_ok=True)
    
    async def process(self, input_data: Any, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Process local drive operation."""
        params = params or {}
        operation = params.get("operation", "list")
        
        operations = {
            "list": self._list_files,
            "upload": self._upload_file,
            "download": self._download_file,
            "delete": self._delete_file,
            "create_folder": self._create_folder,
            "copy": self._copy_file,
            "move": self._move_file,
            "get_metadata": self._get_metadata,
            "search": self._search_files,
            "read": self._read_file,
            "write": self._write_file,
        }
        
        if operation in operations:
            return await operations[operation](input_data, params)
        else:
            return {
                "error": f"Unknown operation: {operation}",
                "available_operations": list(operations.keys()),
                "confidence": 0.0
            }
    
    def _safe_path(self, path: str) -> str:
        """Ensure path is within base directory."""
        # Normalize the path
        if not path.startswith(self.base_path):
            path = os.path.join(self.base_path, path.lstrip("/"))
        
        # Resolve to absolute path
        resolved = os.path.abspath(os.path.realpath(path))
        
        # Security check: ensure path is within base directory
        if not resolved.startswith(os.path.abspath(self.base_path)):
            raise ValueError(f"Path {path} is outside allowed directory")
        
        return resolved
    
    async def _list_files(self, input_data: Any, params: Dict) -> Dict:
        """List files in local directory."""
        folder_path = params.get("folder_path", "/")
        recursive = params.get("recursive", False)
        include_hidden = params.get("include_hidden", False)
        
        try:
            target_path = self._safe_path(folder_path)
            
            if not os.path.exists(target_path):
                return {
                    "operation": "list",
                    "folder_path": folder_path,
                    "error": "Directory not found",
                    "confidence": 0.0
                }
            
            files = []
            
            if recursive:
                for root, dirs, filenames in os.walk(target_path):
                    # Filter hidden directories
                    if not include_hidden:
                        dirs[:] = [d for d in dirs if not d.startswith(".")]
                    
                    for filename in filenames:
                        if not include_hidden and filename.startswith("."):
                            continue
                        
                        file_path = os.path.join(root, filename)
                        rel_path = os.path.relpath(file_path, self.base_path)
                        
                        files.append({
                            "name": filename,
                            "path": rel_path,
                            "size": os.path.getsize(file_path),
                            "modified": os.path.getmtime(file_path),
                            "is_directory": False
                        })
            else:
                for item in os.listdir(target_path):
                    if not include_hidden and item.startswith("."):
                        continue
                    
                    item_path = os.path.join(target_path, item)
                    rel_path = os.path.relpath(item_path, self.base_path)
                    
                    files.append({
                        "name": item,
                        "path": rel_path,
                        "size": os.path.getsize(item_path) if os.path.isfile(item_path) else None,
                        "modified": os.path.getmtime(item_path),
                        "is_directory": os.path.isdir(item_path)
                    })
            
            return {
                "operation": "list",
                "folder_path": folder_path,
                "files": files,
                "file_count": len(files),
                "confidence": 1.0
            }
            
        except Exception as e:
            return {
                "operation": "list",
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _upload_file(self, input_data: Any, params: Dict) -> Dict:
        """Upload/save file to local drive."""
        source_path = self._get_file_path(input_data)
        destination_folder = params.get("folder_path", "/")
        file_name = params.get("file_name", os.path.basename(source_path))
        
        try:
            dest_folder = self._safe_path(destination_folder)
            os.makedirs(dest_folder, exist_ok=True)
            
            dest_path = os.path.join(dest_folder, file_name)
            
            if os.path.exists(source_path):
                shutil.copy2(source_path, dest_path)
            else:
                # If source doesn't exist as file, treat input as content
                content = input_data if isinstance(input_data, (str, bytes)) else str(input_data)
                with open(dest_path, "wb") as f:
                    f.write(content.encode() if isinstance(content, str) else content)
            
            return {
                "operation": "upload",
                "file_name": file_name,
                "file_path": os.path.relpath(dest_path, self.base_path),
                "full_path": dest_path,
                "size": os.path.getsize(dest_path),
                "confidence": 1.0
            }
            
        except Exception as e:
            return {
                "operation": "upload",
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _download_file(self, input_data: Any, params: Dict) -> Dict:
        """Get file from local drive."""
        file_path = params.get("file_path") or self._get_file_path(input_data)
        output_path = params.get("output_path")
        
        try:
            source_path = self._safe_path(file_path)
            
            if not os.path.exists(source_path):
                return {
                    "operation": "download",
                    "error": "File not found",
                    "confidence": 0.0
                }
            
            if os.path.isdir(source_path):
                return {
                    "operation": "download",
                    "error": "Path is a directory, not a file",
                    "confidence": 0.0
                }
            
            if output_path:
                shutil.copy2(source_path, output_path)
                return {
                    "operation": "download",
                    "source_path": os.path.relpath(source_path, self.base_path),
                    "output_path": output_path,
                    "size": os.path.getsize(output_path),
                    "confidence": 1.0
                }
            else:
                # Return file content
                with open(source_path, "rb") as f:
                    content = f.read()
                
                return {
                    "operation": "download",
                    "file_path": os.path.relpath(source_path, self.base_path),
                    "size": len(content),
                    "content_preview": content[:1000].decode("utf-8", errors="replace") if len(content) < 10000 else None,
                    "confidence": 1.0
                }
            
        except Exception as e:
            return {
                "operation": "download",
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _delete_file(self, input_data: Any, params: Dict) -> Dict:
        """Delete file or folder from local drive."""
        file_path = params.get("file_path") or self._get_file_path(input_data)
        recursive = params.get("recursive", False)
        
        try:
            target_path = self._safe_path(file_path)
            
            if not os.path.exists(target_path):
                return {
                    "operation": "delete",
                    "error": "File not found",
                    "confidence": 0.0
                }
            
            if os.path.isdir(target_path):
                if recursive:
                    shutil.rmtree(target_path)
                else:
                    os.rmdir(target_path)
            else:
                os.remove(target_path)
            
            return {
                "operation": "delete",
                "file_path": file_path,
                "deleted": True,
                "confidence": 1.0
            }
            
        except Exception as e:
            return {
                "operation": "delete",
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _create_folder(self, input_data: Any, params: Dict) -> Dict:
        """Create folder in local drive."""
        folder_name = params.get("folder_name", "New Folder")
        parent_path = params.get("parent_path", "/")
        
        try:
            parent = self._safe_path(parent_path)
            new_folder = os.path.join(parent, folder_name)
            
            os.makedirs(new_folder, exist_ok=True)
            
            return {
                "operation": "create_folder",
                "folder_name": folder_name,
                "folder_path": os.path.relpath(new_folder, self.base_path),
                "created": True,
                "confidence": 1.0
            }
            
        except Exception as e:
            return {
                "operation": "create_folder",
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _copy_file(self, input_data: Any, params: Dict) -> Dict:
        """Copy file in local drive."""
        source_path = params.get("source_path") or self._get_file_path(input_data)
        destination_path = params.get("destination_path")
        
        if not destination_path:
            return {
                "operation": "copy",
                "error": "Destination path required",
                "confidence": 0.0
            }
        
        try:
            source = self._safe_path(source_path)
            dest = self._safe_path(destination_path)
            
            if os.path.isdir(source):
                shutil.copytree(source, dest)
            else:
                shutil.copy2(source, dest)
            
            return {
                "operation": "copy",
                "source": os.path.relpath(source, self.base_path),
                "destination": os.path.relpath(dest, self.base_path),
                "copied": True,
                "confidence": 1.0
            }
            
        except Exception as e:
            return {
                "operation": "copy",
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _move_file(self, input_data: Any, params: Dict) -> Dict:
        """Move/rename file in local drive."""
        source_path = params.get("source_path") or self._get_file_path(input_data)
        destination_path = params.get("destination_path")
        
        if not destination_path:
            return {
                "operation": "move",
                "error": "Destination path required",
                "confidence": 0.0
            }
        
        try:
            source = self._safe_path(source_path)
            dest = self._safe_path(destination_path)
            
            shutil.move(source, dest)
            
            return {
                "operation": "move",
                "source": os.path.relpath(source, self.base_path),
                "destination": os.path.relpath(dest, self.base_path),
                "moved": True,
                "confidence": 1.0
            }
            
        except Exception as e:
            return {
                "operation": "move",
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _get_metadata(self, input_data: Any, params: Dict) -> Dict:
        """Get file metadata from local drive."""
        file_path = params.get("file_path") or self._get_file_path(input_data)
        include_hash = params.get("include_hash", False)
        
        try:
            target_path = self._safe_path(file_path)
            
            if not os.path.exists(target_path):
                return {
                    "operation": "get_metadata",
                    "error": "File not found",
                    "confidence": 0.0
                }
            
            stat = os.stat(target_path)
            
            metadata = {
                "operation": "get_metadata",
                "name": os.path.basename(target_path),
                "path": os.path.relpath(target_path, self.base_path),
                "size": stat.st_size,
                "is_directory": os.path.isdir(target_path),
                "created": stat.st_ctime,
                "modified": stat.st_mtime,
                "accessed": stat.st_atime,
            }
            
            if include_hash and os.path.isfile(target_path):
                hasher = hashlib.sha256()
                with open(target_path, "rb") as f:
                    for chunk in iter(lambda: f.read(4096), b""):
                        hasher.update(chunk)
                metadata["sha256_hash"] = hasher.hexdigest()
            
            metadata["confidence"] = 1.0
            return metadata
            
        except Exception as e:
            return {
                "operation": "get_metadata",
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _search_files(self, input_data: Any, params: Dict) -> Dict:
        """Search files in local drive."""
        query = params.get("query", "").lower()
        file_type = params.get("file_type")
        folder_path = params.get("folder_path", "/")
        
        try:
            target_path = self._safe_path(folder_path)
            matches = []
            
            for root, dirs, files in os.walk(target_path):
                for filename in files:
                    if query in filename.lower():
                        file_path = os.path.join(root, filename)
                        
                        # Check file type if specified
                        if file_type and not filename.endswith(f".{file_type}"):
                            continue
                        
                        matches.append({
                            "name": filename,
                            "path": os.path.relpath(file_path, self.base_path),
                            "size": os.path.getsize(file_path),
                            "modified": os.path.getmtime(file_path)
                        })
            
            return {
                "operation": "search",
                "query": query,
                "matches": matches,
                "match_count": len(matches),
                "confidence": 1.0
            }
            
        except Exception as e:
            return {
                "operation": "search",
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _read_file(self, input_data: Any, params: Dict) -> Dict:
        """Read file content from local drive."""
        file_path = params.get("file_path") or self._get_file_path(input_data)
        encoding = params.get("encoding", "utf-8")
        
        try:
            target_path = self._safe_path(file_path)
            
            with open(target_path, "r", encoding=encoding, errors="replace") as f:
                content = f.read()
            
            return {
                "operation": "read",
                "file_path": os.path.relpath(target_path, self.base_path),
                "content": content,
                "size": len(content),
                "confidence": 1.0
            }
            
        except Exception as e:
            return {
                "operation": "read",
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _write_file(self, input_data: Any, params: Dict) -> Dict:
        """Write content to file in local drive."""
        file_path = params.get("file_path")
        content = params.get("content") or input_data
        encoding = params.get("encoding", "utf-8")
        append = params.get("append", False)
        
        if not file_path:
            return {
                "operation": "write",
                "error": "File path required",
                "confidence": 0.0
            }
        
        try:
            target_path = self._safe_path(file_path)
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            
            mode = "a" if append else "w"
            with open(target_path, mode, encoding=encoding) as f:
                f.write(content if isinstance(content, str) else str(content))
            
            return {
                "operation": "write",
                "file_path": os.path.relpath(target_path, self.base_path),
                "bytes_written": len(content.encode(encoding)),
                "appended": append,
                "confidence": 1.0
            }
            
        except Exception as e:
            return {
                "operation": "write",
                "error": str(e),
                "confidence": 0.0
            }
    
    def _get_file_path(self, input_data: Any) -> str:
        """Extract file path from input."""
        if isinstance(input_data, str):
            return input_data
        if isinstance(input_data, dict):
            if "file_path" in input_data:
                return input_data["file_path"]
            if "path" in input_data:
                return input_data["path"]
        return "/"
