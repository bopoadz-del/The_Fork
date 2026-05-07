"""Android Drive Block - Integration with Android storage."""

import os
import base64
from typing import Any, Dict, List, Optional
from app.core.block import BaseBlock, BlockConfig


class AndroidDriveBlock(BaseBlock):
    """Android storage integration for file access and management."""
    
    def __init__(self):
        super().__init__(BlockConfig(
            name="android_drive",
            version="1.0",
            description="Android storage file access and management",
            supported_inputs=["file", "uri"],
            supported_outputs=["uri", "metadata"]
        ,
            layer=4,
            tags=["integration", "storage", "mobile"]))
        self.android_root = os.getenv("ANDROID_ROOT", "/sdcard")
        self.app_data_path = os.getenv("ANDROID_APP_DATA", "/data/data/com.cerebrum.app/files")
        self.shared_storage = os.getenv("ANDROID_SHARED_STORAGE", "/sdcard")
    
    async def process(self, input_data: Any, params: Dict[str, Any] = None) -> Dict[str, Any]:
        params = params or {}
        operation = params.get("operation", "list")
        
        operations = {
            "list": self._list_files,
            "read": self._read_file,
            "write": self._write_file,
            "delete": self._delete_file,
            "create_folder": self._create_folder,
            "get_metadata": self._get_metadata,
            "scan_media": self._scan_media,
            "get_paths": self._get_paths,
        }
        
        if operation in operations:
            return await operations[operation](input_data, params)
        else:
            return {
                "error": f"Unknown operation: {operation}",
                "available_operations": list(operations.keys()),
                "confidence": 0.0
            }
    
    def _is_android_environment(self) -> bool:
        return os.path.exists("/system/build.prop") or "ANDROID_ROOT" in os.environ
    
    def _safe_path(self, path: str) -> str:
        if path.startswith("content://"):
            return path
        if path.startswith("file://"):
            path = path[7:]
        if not path.startswith("/"):
            path = os.path.join(self.android_root, path)
        return path
    
    def _guess_mime_type(self, filename: str) -> str:
        ext = os.path.splitext(filename)[1].lower()
        mime_types = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".gif": "image/gif", ".mp4": "video/mp4", ".mp3": "audio/mpeg",
            ".pdf": "application/pdf", ".txt": "text/plain",
            ".json": "application/json", ".html": "text/html",
        }
        return mime_types.get(ext, "application/octet-stream")
    
    async def _list_files(self, input_data: Any, params: Dict) -> Dict:
        folder_path = params.get("folder_path", "/sdcard")
        media_type = params.get("media_type")
        
        media_folders = {
            "images": "/sdcard/Pictures",
            "photos": "/sdcard/DCIM",
            "videos": "/sdcard/Movies",
            "audio": "/sdcard/Music",
            "downloads": "/sdcard/Download",
            "documents": "/sdcard/Documents",
        }
        
        if media_type and media_type in media_folders:
            folder_path = media_folders[media_type]
        
        target_path = self._safe_path(folder_path)
        
        if not self._is_android_environment():
            return self._mock_response("list", params, folder_path=folder_path)
        
        try:
            files = []
            if os.path.exists(target_path):
                for item in os.listdir(target_path):
                    item_path = os.path.join(target_path, item)
                    files.append({
                        "name": item,
                        "path": item_path,
                        "uri": f"file://{item_path}",
                        "size": os.path.getsize(item_path) if os.path.isfile(item_path) else None,
                        "modified": os.path.getmtime(item_path),
                        "is_directory": os.path.isdir(item_path),
                        "mime_type": self._guess_mime_type(item)
                    })
            
            return {
                "operation": "list",
                "folder_path": folder_path,
                "files": files,
                "file_count": len(files),
                "is_android_env": True,
                "confidence": 1.0
            }
        except Exception as e:
            return {"operation": "list", "error": str(e), "confidence": 0.0}
    
    async def _read_file(self, input_data: Any, params: Dict) -> Dict:
        file_uri = params.get("uri") or params.get("file_path") or input_data
        as_base64 = params.get("as_base64", False)
        target_path = self._safe_path(file_uri)
        
        if not self._is_android_environment():
            return self._mock_response("read", params, uri=file_uri)
        
        try:
            if file_uri.startswith("content://"):
                return {"operation": "read", "uri": file_uri, "note": "Content URIs require Android ContentResolver", "confidence": 0.5}
            
            if not os.path.exists(target_path):
                return {"operation": "read", "error": "File not found", "confidence": 0.0}
            
            if as_base64:
                with open(target_path, "rb") as f:
                    content = base64.b64encode(f.read()).decode("utf-8")
                content_type = "base64"
            else:
                try:
                    with open(target_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    content_type = "text"
                except UnicodeDecodeError:
                    with open(target_path, "rb") as f:
                        data = f.read()
                    content = base64.b64encode(data[:1000]).decode("utf-8")
                    content_type = "binary_preview"
            
            return {
                "operation": "read",
                "uri": file_uri,
                "path": target_path,
                "content": content,
                "content_type": content_type,
                "size": os.path.getsize(target_path),
                "mime_type": self._guess_mime_type(target_path),
                "confidence": 1.0
            }
        except Exception as e:
            return {"operation": "read", "error": str(e), "confidence": 0.0}
    
    async def _write_file(self, input_data: Any, params: Dict) -> Dict:
        file_uri = params.get("uri") or params.get("file_path")
        content = params.get("content") or input_data
        folder = params.get("folder", "/sdcard/Download")
        filename = params.get("filename")
        is_base64 = params.get("is_base64", False)
        mime_type = params.get("mime_type", "application/octet-stream")
        
        if not filename:
            return {"operation": "write", "error": "Filename required", "confidence": 0.0}
        
        target_folder = self._safe_path(folder)
        target_path = os.path.join(target_folder, filename)
        
        if not self._is_android_environment():
            return self._mock_response("write", params, folder=folder, filename=filename)
        
        try:
            os.makedirs(target_folder, exist_ok=True)
            
            mode = "wb" if is_base64 else "w"
            encoding = None if is_base64 else "utf-8"
            
            with open(target_path, mode, encoding=encoding) as f:
                if is_base64 and isinstance(content, str):
                    f.write(base64.b64decode(content))
                else:
                    f.write(content)
            
            return {
                "operation": "write",
                "uri": f"file://{target_path}",
                "path": target_path,
                "filename": filename,
                "size": os.path.getsize(target_path),
                "mime_type": mime_type,
                "confidence": 1.0
            }
        except Exception as e:
            return {"operation": "write", "error": str(e), "confidence": 0.0}
    
    async def _delete_file(self, input_data: Any, params: Dict) -> Dict:
        file_uri = params.get("uri") or params.get("file_path") or input_data
        target_path = self._safe_path(file_uri)
        
        if not self._is_android_environment():
            return self._mock_response("delete", params, uri=file_uri)
        
        try:
            if os.path.exists(target_path):
                import shutil
                if os.path.isdir(target_path):
                    shutil.rmtree(target_path)
                else:
                    os.remove(target_path)
                return {"operation": "delete", "uri": file_uri, "deleted": True, "confidence": 1.0}
            else:
                return {"operation": "delete", "error": "File not found", "confidence": 0.0}
        except Exception as e:
            return {"operation": "delete", "error": str(e), "confidence": 0.0}
    
    async def _create_folder(self, input_data: Any, params: Dict) -> Dict:
        folder_name = params.get("folder_name", "NewFolder")
        parent_path = params.get("parent_path", "/sdcard")
        
        target_parent = self._safe_path(parent_path)
        target_path = os.path.join(target_parent, folder_name)
        
        if not self._is_android_environment():
            return self._mock_response("create_folder", params, folder_name=folder_name)
        
        try:
            os.makedirs(target_path, exist_ok=True)
            return {
                "operation": "create_folder",
                "uri": f"file://{target_path}",
                "path": target_path,
                "folder_name": folder_name,
                "created": True,
                "confidence": 1.0
            }
        except Exception as e:
            return {"operation": "create_folder", "error": str(e), "confidence": 0.0}
    
    async def _get_metadata(self, input_data: Any, params: Dict) -> Dict:
        file_uri = params.get("uri") or params.get("file_path") or input_data
        target_path = self._safe_path(file_uri)
        
        if not self._is_android_environment():
            return self._mock_response("get_metadata", params, uri=file_uri)
        
        try:
            if not os.path.exists(target_path):
                return {"operation": "get_metadata", "error": "File not found", "confidence": 0.0}
            
            stat = os.stat(target_path)
            return {
                "operation": "get_metadata",
                "uri": file_uri,
                "path": target_path,
                "name": os.path.basename(target_path),
                "size": stat.st_size,
                "is_directory": os.path.isdir(target_path),
                "created": stat.st_ctime,
                "modified": stat.st_mtime,
                "mime_type": self._guess_mime_type(target_path),
                "confidence": 1.0
            }
        except Exception as e:
            return {"operation": "get_metadata", "error": str(e), "confidence": 0.0}
    
    async def _scan_media(self, input_data: Any, params: Dict) -> Dict:
        media_type = params.get("media_type", "all")
        media_folders = {
            "images": ["/sdcard/DCIM", "/sdcard/Pictures"],
            "video": ["/sdcard/Movies", "/sdcard/DCIM"],
            "audio": ["/sdcard/Music", "/sdcard/Recordings"],
        }
        
        if not self._is_android_environment():
            return self._mock_response("scan_media", params, media_type=media_type)
        
        try:
            files = []
            if media_type == "all":
                folders_to_scan = sum(media_folders.values(), [])
            else:
                folders_to_scan = media_folders.get(media_type, ["/sdcard"])
            
            for folder in folders_to_scan:
                folder_path = self._safe_path(folder)
                if os.path.exists(folder_path):
                    for root, dirs, filenames in os.walk(folder_path):
                        for filename in filenames:
                            file_path = os.path.join(root, filename)
                            files.append({
                                "name": filename,
                                "path": file_path,
                                "uri": f"file://{file_path}",
                                "size": os.path.getsize(file_path),
                                "modified": os.path.getmtime(file_path),
                                "mime_type": self._guess_mime_type(filename)
                            })
            
            return {
                "operation": "scan_media",
                "media_type": media_type,
                "files": files,
                "file_count": len(files),
                "confidence": 1.0
            }
        except Exception as e:
            return {"operation": "scan_media", "error": str(e), "confidence": 0.0}
    
    async def _get_paths(self, input_data: Any, params: Dict) -> Dict:
        return {
            "operation": "get_paths",
            "is_android_env": self._is_android_environment(),
            "paths": {
                "shared_storage": self.shared_storage,
                "app_data": self.app_data_path,
                "pictures": f"{self.shared_storage}/Pictures",
                "dcim": f"{self.shared_storage}/DCIM",
                "downloads": f"{self.shared_storage}/Download",
                "documents": f"{self.shared_storage}/Documents",
                "movies": f"{self.shared_storage}/Movies",
                "music": f"{self.shared_storage}/Music",
            },
            "confidence": 1.0
        }
    
    def _mock_response(self, operation: str, params: Dict, **kwargs) -> Dict:
        return {
            "operation": operation,
            "mock": True,
            "message": "Not in Android environment. These operations work on Android devices.",
            "params": params,
            **kwargs,
            "confidence": 0.5
        }
