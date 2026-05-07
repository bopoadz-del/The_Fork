"""Google Drive Block - Integration with Google Drive."""

import os
import io
import base64
from typing import Any, Dict, List, Optional
from app.core.block import BaseBlock, BlockConfig


class GoogleDriveBlock(BaseBlock):
    """Google Drive integration for file upload, download, and management."""
    
    def __init__(self):
        super().__init__(BlockConfig(
            name="google_drive",
            version="1.0",
            description="Google Drive file upload, download, and management",
            requires_api_key=True,
            supported_inputs=["file", "file_path"],
            supported_outputs=["file_id", "url", "metadata"]
        ,
            layer=4,
            tags=["integration", "storage", "cloud"],
            requires=["auth"]))
        self._google_auth_available = self._check_google_auth()
        self._drive_service = None
    
    def _check_google_auth(self) -> bool:
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            return True
        except ImportError:
            return False
    
    def _get_service(self):
        """Get or create Google Drive service."""
        if self._drive_service:
            return self._drive_service
        
        if not self._google_auth_available:
            raise RuntimeError("Google auth libraries not installed")
        
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        
        # Check for credentials
        creds = None
        token_path = os.getenv("GOOGLE_TOKEN_PATH", "token.json")
        credentials_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
        
        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path)
        
        if not creds or not creds.valid:
            raise RuntimeError("Google credentials not valid. Run OAuth flow first.")
        
        self._drive_service = build("drive", "v3", credentials=creds)
        return self._drive_service
    
    async def process(self, input_data: Any, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Process Google Drive operation."""
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
        """List files in Google Drive."""
        folder_id = params.get("folder_id", "root")
        page_size = params.get("page_size", 100)
        
        if not self._google_auth_available:
            return self._mock_response("list", params)
        
        try:
            service = self._get_service()
            
            query = f"'{folder_id}' in parents and trashed = false"
            
            results = service.files().list(
                q=query,
                pageSize=page_size,
                fields="nextPageToken, files(id, name, mimeType, size, createdTime, modifiedTime, webViewLink)"
            ).execute()
            
            files = results.get("files", [])
            
            return {
                "operation": "list",
                "folder_id": folder_id,
                "files": files,
                "file_count": len(files),
                "confidence": 0.95
            }
            
        except Exception as e:
            return {
                "operation": "list",
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _upload_file(self, input_data: Any, params: Dict) -> Dict:
        """Upload file to Google Drive."""
        file_path = self._get_file_path(input_data)
        folder_id = params.get("folder_id", "root")
        file_name = params.get("file_name", os.path.basename(file_path))
        mime_type = params.get("mime_type", "application/octet-stream")
        convert = params.get("convert", False)  # Convert to Google Docs format
        
        if not os.path.exists(file_path):
            return {
                "error": f"File not found: {file_path}",
                "confidence": 0.0
            }
        
        if not self._google_auth_available:
            return self._mock_response("upload", params, file_name=file_name)
        
        try:
            from googleapiclient.http import MediaFileUpload
            
            service = self._get_service()
            
            file_metadata = {
                "name": file_name,
                "parents": [folder_id]
            }
            
            media = MediaFileUpload(file_path, mimetype=mime_type, resumable=True)
            
            file = service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id, name, webViewLink, webContentLink"
            ).execute()
            
            return {
                "operation": "upload",
                "file_id": file.get("id"),
                "file_name": file.get("name"),
                "web_view_link": file.get("webViewLink"),
                "web_content_link": file.get("webContentLink"),
                "confidence": 0.95
            }
            
        except Exception as e:
            return {
                "operation": "upload",
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _download_file(self, input_data: Any, params: Dict) -> Dict:
        """Download file from Google Drive."""
        file_id = params.get("file_id") or (input_data if isinstance(input_data, str) else None)
        output_path = params.get("output_path", f"/app/data/{file_id}")
        
        if not file_id:
            return {
                "error": "File ID required",
                "confidence": 0.0
            }
        
        if not self._google_auth_available:
            return self._mock_response("download", params, file_id=file_id)
        
        try:
            from googleapiclient.http import MediaIoBaseDownload
            
            service = self._get_service()
            
            request = service.files().get_media(fileId=file_id)
            
            with open(output_path, "wb") as f:
                downloader = MediaIoBaseDownload(f, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
            
            return {
                "operation": "download",
                "file_id": file_id,
                "output_path": output_path,
                "file_size": os.path.getsize(output_path),
                "confidence": 0.95
            }
            
        except Exception as e:
            return {
                "operation": "download",
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _delete_file(self, input_data: Any, params: Dict) -> Dict:
        """Delete file from Google Drive."""
        file_id = params.get("file_id") or (input_data if isinstance(input_data, str) else None)
        
        if not file_id:
            return {
                "error": "File ID required",
                "confidence": 0.0
            }
        
        if not self._google_auth_available:
            return self._mock_response("delete", params, file_id=file_id)
        
        try:
            service = self._get_service()
            service.files().delete(fileId=file_id).execute()
            
            return {
                "operation": "delete",
                "file_id": file_id,
                "deleted": True,
                "confidence": 0.95
            }
            
        except Exception as e:
            return {
                "operation": "delete",
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _create_folder(self, input_data: Any, params: Dict) -> Dict:
        """Create folder in Google Drive."""
        folder_name = params.get("folder_name", "New Folder")
        parent_id = params.get("parent_id", "root")
        
        if not self._google_auth_available:
            return self._mock_response("create_folder", params, folder_name=folder_name)
        
        try:
            service = self._get_service()
            
            file_metadata = {
                "name": folder_name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id]
            }
            
            file = service.files().create(body=file_metadata, fields="id, name").execute()
            
            return {
                "operation": "create_folder",
                "folder_id": file.get("id"),
                "folder_name": file.get("name"),
                "confidence": 0.95
            }
            
        except Exception as e:
            return {
                "operation": "create_folder",
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _share_file(self, input_data: Any, params: Dict) -> Dict:
        """Share file in Google Drive."""
        file_id = params.get("file_id")
        email = params.get("email")
        role = params.get("role", "reader")  # reader, commenter, writer
        
        if not file_id:
            return {
                "error": "File ID required",
                "confidence": 0.0
            }
        
        if not self._google_auth_available:
            return self._mock_response("share", params, file_id=file_id)
        
        try:
            service = self._get_service()
            
            permission = {
                "type": "user" if email else "anyone",
                "role": role
            }
            
            if email:
                permission["emailAddress"] = email
            
            service.permissions().create(
                fileId=file_id,
                body=permission
            ).execute()
            
            return {
                "operation": "share",
                "file_id": file_id,
                "shared_with": email or "anyone",
                "role": role,
                "confidence": 0.95
            }
            
        except Exception as e:
            return {
                "operation": "share",
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _get_metadata(self, input_data: Any, params: Dict) -> Dict:
        """Get file metadata from Google Drive."""
        file_id = params.get("file_id") or (input_data if isinstance(input_data, str) else None)
        
        if not file_id:
            return {
                "error": "File ID required",
                "confidence": 0.0
            }
        
        if not self._google_auth_available:
            return self._mock_response("get_metadata", params, file_id=file_id)
        
        try:
            service = self._get_service()
            
            file = service.files().get(
                fileId=file_id,
                fields="id, name, mimeType, size, createdTime, modifiedTime, webViewLink, owners, shared"
            ).execute()
            
            return {
                "operation": "get_metadata",
                "file_id": file.get("id"),
                "name": file.get("name"),
                "mime_type": file.get("mimeType"),
                "size": file.get("size"),
                "created_time": file.get("createdTime"),
                "modified_time": file.get("modifiedTime"),
                "web_view_link": file.get("webViewLink"),
                "is_shared": file.get("shared"),
                "confidence": 0.95
            }
            
        except Exception as e:
            return {
                "operation": "get_metadata",
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _search_files(self, input_data: Any, params: Dict) -> Dict:
        """Search files in Google Drive."""
        query = params.get("query", "")
        file_type = params.get("file_type")
        page_size = params.get("page_size", 50)
        
        if not self._google_auth_available:
            return self._mock_response("search", params)
        
        try:
            service = self._get_service()
            
            # Build search query
            search_query = f"name contains '{query}' and trashed = false"
            
            if file_type:
                mime_types = {
                    "document": "application/vnd.google-apps.document",
                    "spreadsheet": "application/vnd.google-apps.spreadsheet",
                    "presentation": "application/vnd.google-apps.presentation",
                    "pdf": "application/pdf",
                    "image": "image/",
                    "video": "video/",
                    "folder": "application/vnd.google-apps.folder"
                }
                if file_type in mime_types:
                    search_query += f" and mimeType contains '{mime_types[file_type]}'"
            
            results = service.files().list(
                q=search_query,
                pageSize=page_size,
                fields="files(id, name, mimeType, size, modifiedTime, webViewLink)"
            ).execute()
            
            files = results.get("files", [])
            
            return {
                "operation": "search",
                "query": query,
                "files": files,
                "file_count": len(files),
                "confidence": 0.95
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
        """Return mock response when Google auth is not available."""
        return {
            "operation": operation,
            "mock": True,
            "message": "Google Drive integration not configured. Install google-auth and google-api-python-client.",
            "params": params,
            **kwargs,
            "confidence": 0.5
        }
