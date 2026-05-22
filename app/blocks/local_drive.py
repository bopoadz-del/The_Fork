"""Local Drive Block - Local filesystem access"""

import os
from typing import Any, Dict
from app.core.universal_base import UniversalBlock


class LocalDriveBlock(UniversalBlock):
    """Local filesystem operations"""
    
    name = "local_drive"
    version = "1.0"
    description = "Local filesystem access: list, read, write files"
    layer = 4
    tags = ["integration", "storage", "local"]
    requires = []
    
    ui_schema = {
        "input": {
            "type": "file",
            "accept": ["*/*"],
            "placeholder": "Browse local files...",
            "multiline": False
        },
        "output": {
            "type": "list",
            "fields": [
                {"name": "files", "type": "array", "label": "Files"},
                {"name": "path", "type": "text", "label": "Path"}
            ]
        },
        "quick_actions": [
            {"icon": "📁", "label": "Browse Local", "prompt": "List local files"}
        ]
    }
    
    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        """List, read, or write local files"""
        params = params or {}
        operation = params.get("operation", "list")
        # Accept the target directory from input_data or any of the common
        # param keys (folder_path / path) — callers use both interchangeably.
        path = input_data if isinstance(input_data, str) else (
            params.get("folder_path") or params.get("path") or "./"
        )
        
        try:
            if operation == "write":
                file_path = params.get("file_path", "/tmp/test.txt")
                content = params.get("content", "")
                with open(file_path, "w") as f:
                    f.write(content)
                return {"status": "success", "operation": "write", "file_path": file_path, "bytes_written": len(content)}
            elif operation == "read":
                file_path = params.get("file_path", path)
                with open(file_path, "r") as f:
                    content = f.read()
                return {"status": "success", "operation": "read", "file_path": file_path, "content": content}
            else:
                if not os.path.isdir(path):
                    return {
                        "status": "error",
                        "operation": "list",
                        "path": path,
                        "error": f"Not a directory: {path}",
                    }
                files = os.listdir(path)
                return {
                    "status": "success",
                    "operation": "list",
                    "path": path,
                    "files": files[:20]  # Limit results
                }
        except Exception as e:
            return {"status": "error", "error": str(e)}
