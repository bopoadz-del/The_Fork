"""OneDrive Block - Integration with Microsoft OneDrive."""

import os
import io
from typing import Any, Dict, List, Optional
from app.core.block import BaseBlock, BlockConfig
import aiohttp


class OneDriveBlock(BaseBlock):
    """Microsoft OneDrive integration for file upload, download, and management."""
    
    def __init__(self):
        super().__init__(BlockConfig(
            name="onedrive",
            version="1.0",
            description="Microsoft OneDrive file upload, download, and management",
            requires_api_key=True,
            supported_inputs=["file", "file_path"],
            supported_outputs=["file_id", "url", "metadata"]
        ,
            layer=4,
            tags=["integration", "storage", "cloud"],
            requires=["auth"]))
        self._msal_available = self._check_msal()
        self.access_token = os.getenv("ONEDRIVE_ACCESS_TOKEN")
    
    def _check_msal(self) -> bool:
        try:
            import msal
            return True
        except ImportError:
            return False
    
    def _get_headers(self) -> Dict:
        """Get authorization headers."""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
    
    async def process(self, input_data: Any, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Process OneDrive operation."""
        params = params or {}
        operation = params.get("operation", "list")
        
        operations = {
            "list": self._list_files,
            "upload": self._upload_file,
            "download": self._download_file,
            "delete": self._delete_file,
            "create_folder": self._create_folder,
            "share": self._share_file,
            "get_metadata": self._get_metadata,
            "search": self._search_files,
        }
        
        if operation in operations:
            return await operations[operation](input_data, params)
        else:
            return {
                "error": f"Unknown operation: {operation}",
                "available_operations": list(operations.keys()),
                "confidence": 0.0
            }
    
    async def _list_files(self, input_data: Any, params: Dict) -> Dict:
        """List files in OneDrive."""
        folder_path = params.get("folder_path", "/")
        
        if not self.access_token:
            return self._mock_response("list", params)
        
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://graph.microsoft.com/v1.0/me/drive/root:{folder_path}:/children"
                
                async with session.get(url, headers=self._get_headers()) as response:
                    if response.status == 200:
                        data = await response.json()
                        files = data.get("value", [])
                        
                        return {
                            "operation": "list",
                            "folder_path": folder_path,
                            "files": [
                                {
                                    "id": f.get("id"),
                                    "name": f.get("name"),
                                    "size": f.get("size"),
                                    "mime_type": f.get("file", {}).get("mimeType") if f.get("file") else "folder",
                                    "created": f.get("createdDateTime"),
                                    "modified": f.get("lastModifiedDateTime"),
                                    "web_url": f.get("webUrl")
                                }
                                for f in files
                            ],
                            "file_count": len(files),
                            "confidence": 0.95
                        }
                    else:
                        error = await response.text()
                        return {
                            "operation": "list",
                            "error": f"HTTP {response.status}: {error}",
                            "confidence": 0.0
                        }
                        
        except Exception as e:
            return {
                "operation": "list",
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _upload_file(self, input_data: Any, params: Dict) -> Dict:
        """Upload file to OneDrive."""
        file_path = self._get_file_path(input_data)
        folder_path = params.get("folder_path", "/")
        file_name = params.get("file_name", os.path.basename(file_path))
        
        if not os.path.exists(file_path):
            return {
                "error": f"File not found: {file_path}",
                "confidence": 0.0
            }
        
        if not self.access_token:
            return self._mock_response("upload", params, file_name=file_name)
        
        try:
            async with aiohttp.ClientSession() as session:
                # Read file content
                with open(file_path, "rb") as f:
                    file_content = f.read()
                
                # Upload URL
                upload_path = f"{folder_path}/{file_name}".replace("//", "/")
                url = f"https://graph.microsoft.com/v1.0/me/drive/root:{upload_path}:/content"
                
                headers = {
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/octet-stream"
                }
                
                async with session.put(url, headers=headers, data=file_content) as response:
                    if response.status in (200, 201):
                        data = await response.json()
                        
                        return {
                            "operation": "upload",
                            "file_id": data.get("id"),
                            "file_name": data.get("name"),
                            "web_url": data.get("webUrl"),
                            "size": data.get("size"),
                            "confidence": 0.95
                        }
                    else:
                        error = await response.text()
                        return {
                            "operation": "upload",
                            "error": f"HTTP {response.status}: {error}",
                            "confidence": 0.0
                        }
                        
        except Exception as e:
            return {
                "operation": "upload",
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _download_file(self, input_data: Any, params: Dict) -> Dict:
        """Download file from OneDrive."""
        file_id = params.get("file_id")
        file_path = params.get("file_path")
        output_path = params.get("output_path")
        
        if not file_id and not file_path:
            return {
                "error": "File ID or file path required",
                "confidence": 0.0
            }
        
        if not self.access_token:
            return self._mock_response("download", params)
        
        try:
            async with aiohttp.ClientSession() as session:
                if file_id:
                    url = f"https://graph.microsoft.com/v1.0/me/drive/items/{file_id}/content"
                else:
                    url = f"https://graph.microsoft.com/v1.0/me/drive/root:{file_path}:/content"
                
                headers = {"Authorization": f"Bearer {self.access_token}"}
                
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        content = await response.read()
                        
                        # Determine output path
                        if not output_path:
                            output_path = f"/app/data/onedrive_{file_id or 'download'}"
                        
                        with open(output_path, "wb") as f:
                            f.write(content)
                        
                        return {
                            "operation": "download",
                            "file_id": file_id,
                            "file_path": file_path,
                            "output_path": output_path,
                            "size": len(content),
                            "confidence": 0.95
                        }
                    else:
                        error = await response.text()
                        return {
                            "operation": "download",
                            "error": f"HTTP {response.status}: {error}",
                            "confidence": 0.0
                        }
                        
        except Exception as e:
            return {
                "operation": "download",
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _delete_file(self, input_data: Any, params: Dict) -> Dict:
        """Delete file from OneDrive."""
        file_id = params.get("file_id")
        file_path = params.get("file_path")
        
        if not file_id and not file_path:
            return {
                "error": "File ID or file path required",
                "confidence": 0.0
            }
        
        if not self.access_token:
            return self._mock_response("delete", params)
        
        try:
            async with aiohttp.ClientSession() as session:
                if file_id:
                    url = f"https://graph.microsoft.com/v1.0/me/drive/items/{file_id}"
                else:
                    url = f"https://graph.microsoft.com/v1.0/me/drive/root:{file_path}"
                
                async with session.delete(url, headers=self._get_headers()) as response:
                    if response.status == 204:
                        return {
                            "operation": "delete",
                            "deleted": True,
                            "confidence": 0.95
                        }
                    else:
                        error = await response.text()
                        return {
                            "operation": "delete",
                            "error": f"HTTP {response.status}: {error}",
                            "confidence": 0.0
                        }
                        
        except Exception as e:
            return {
                "operation": "delete",
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _create_folder(self, input_data: Any, params: Dict) -> Dict:
        """Create folder in OneDrive."""
        folder_name = params.get("folder_name", "New Folder")
        parent_path = params.get("parent_path", "/")
        
        if not self.access_token:
            return self._mock_response("create_folder", params, folder_name=folder_name)
        
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://graph.microsoft.com/v1.0/me/drive/root:{parent_path}:/children"
                
                body = {
                    "name": folder_name,
                    "folder": {},
                    "@microsoft.graph.conflictBehavior": "rename"
                }
                
                async with session.post(url, headers=self._get_headers(), json=body) as response:
                    if response.status == 201:
                        data = await response.json()
                        
                        return {
                            "operation": "create_folder",
                            "folder_id": data.get("id"),
                            "folder_name": data.get("name"),
                            "web_url": data.get("webUrl"),
                            "confidence": 0.95
                        }
                    else:
                        error = await response.text()
                        return {
                            "operation": "create_folder",
                            "error": f"HTTP {response.status}: {error}",
                            "confidence": 0.0
                        }
                        
        except Exception as e:
            return {
                "operation": "create_folder",
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _share_file(self, input_data: Any, params: Dict) -> Dict:
        """Create sharing link for OneDrive file."""
        file_id = params.get("file_id")
        file_path = params.get("file_path")
        link_type = params.get("link_type", "view")  # view, edit
        scope = params.get("scope", "anonymous")  # anonymous, organization
        
        if not file_id and not file_path:
            return {
                "error": "File ID or file path required",
                "confidence": 0.0
            }
        
        if not self.access_token:
            return self._mock_response("share", params)
        
        try:
            async with aiohttp.ClientSession() as session:
                if file_id:
                    url = f"https://graph.microsoft.com/v1.0/me/drive/items/{file_id}/createLink"
                else:
                    url = f"https://graph.microsoft.com/v1.0/me/drive/root:{file_path}:/createLink"
                
                body = {
                    "type": link_type,
                    "scope": scope
                }
                
                async with session.post(url, headers=self._get_headers(), json=body) as response:
                    if response.status == 201:
                        data = await response.json()
                        link = data.get("link", {})
                        
                        return {
                            "operation": "share",
                            "share_url": link.get("webUrl"),
                            "link_type": link_type,
                            "scope": scope,
                            "confidence": 0.95
                        }
                    else:
                        error = await response.text()
                        return {
                            "operation": "share",
                            "error": f"HTTP {response.status}: {error}",
                            "confidence": 0.0
                        }
                        
        except Exception as e:
            return {
                "operation": "share",
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _get_metadata(self, input_data: Any, params: Dict) -> Dict:
        """Get file metadata from OneDrive."""
        file_id = params.get("file_id")
        file_path = params.get("file_path")
        
        if not file_id and not file_path:
            return {
                "error": "File ID or file path required",
                "confidence": 0.0
            }
        
        if not self.access_token:
            return self._mock_response("get_metadata", params)
        
        try:
            async with aiohttp.ClientSession() as session:
                if file_id:
                    url = f"https://graph.microsoft.com/v1.0/me/drive/items/{file_id}"
                else:
                    url = f"https://graph.microsoft.com/v1.0/me/drive/root:{file_path}"
                
                async with session.get(url, headers=self._get_headers()) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        return {
                            "operation": "get_metadata",
                            "file_id": data.get("id"),
                            "name": data.get("name"),
                            "size": data.get("size"),
                            "mime_type": data.get("file", {}).get("mimeType"),
                            "created": data.get("createdDateTime"),
                            "modified": data.get("lastModifiedDateTime"),
                            "web_url": data.get("webUrl"),
                            "confidence": 0.95
                        }
                    else:
                        error = await response.text()
                        return {
                            "operation": "get_metadata",
                            "error": f"HTTP {response.status}: {error}",
                            "confidence": 0.0
                        }
                        
        except Exception as e:
            return {
                "operation": "get_metadata",
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _search_files(self, input_data: Any, params: Dict) -> Dict:
        """Search files in OneDrive."""
        query = params.get("query", "")
        
        if not self.access_token:
            return self._mock_response("search", params)
        
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://graph.microsoft.com/v1.0/me/drive/search(q='{query}')"
                
                async with session.get(url, headers=self._get_headers()) as response:
                    if response.status == 200:
                        data = await response.json()
                        files = data.get("value", [])
                        
                        return {
                            "operation": "search",
                            "query": query,
                            "files": [
                                {
                                    "id": f.get("id"),
                                    "name": f.get("name"),
                                    "web_url": f.get("webUrl")
                                }
                                for f in files
                            ],
                            "file_count": len(files),
                            "confidence": 0.95
                        }
                    else:
                        error = await response.text()
                        return {
                            "operation": "search",
                            "error": f"HTTP {response.status}: {error}",
                            "confidence": 0.0
                        }
                        
        except Exception as e:
            return {
                "operation": "search",
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
            if "source_id" in input_data:
                return f"/app/data/{input_data['source_id']}"
        raise ValueError("Invalid input: expected file path")
    
    def _mock_response(self, operation: str, params: Dict, **kwargs) -> Dict:
        """Return mock response when OneDrive auth is not available."""
        return {
            "operation": operation,
            "mock": True,
            "message": "OneDrive integration not configured. Set ONEDRIVE_ACCESS_TOKEN environment variable.",
            "params": params,
            **kwargs,
            "confidence": 0.5
        }
