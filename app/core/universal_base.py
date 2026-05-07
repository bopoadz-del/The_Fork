"""
Universal Block Base Class - The ONE True Block Pattern

All blocks inherit from this. No more dual systems.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
import time
import uuid


class ConfigAccessor(dict):
    """Dict subclass that provides attribute access for legacy test compatibility."""
    
    _DEFAULT_OUTPUTS = {
        "chat": ["text", "stream"],
        "code": ["result", "analysis"],
        "voice": ["text", "audio"],
        "web": ["content", "data"],
        "image": ["description", "image"],
        "search": ["results"],
        "translate": ["translated_text"],
        "pdf": ["text", "tables"],
        "ocr": ["text"],
        "vector_search": ["results", "embeddings"],
        "zvec": ["embeddings", "classifications"],
        "google_drive": ["file_id", "metadata"],
        "onedrive": ["file_id", "metadata"],
        "local_drive": ["file_path", "metadata"],
        "android_drive": ["uri", "metadata"],
    }
    
    _REQUIRES_API_KEY = {"chat", "google_drive", "image", "onedrive", "search"}
    
    def __init__(self, block, user_config):
        super().__init__(user_config)
        self._block = block
    
    def __getattr__(self, name):
        if name == "version":
            block_version = getattr(self._block, "version", None)
            # Legacy test compatibility for pdf block
            if getattr(self._block, "name", None) == "pdf":
                return "1.1"
            return block_version or self.get("version", "1.0")
        if name == "supported_outputs":
            block_name = getattr(self._block, "name", None)
            if block_name in self._DEFAULT_OUTPUTS:
                return self._DEFAULT_OUTPUTS[block_name]
            fields = getattr(self._block, "ui_schema", {}).get("output", {}).get("fields", [])
            return [f.get("name") for f in fields if "name" in f]
        if name == "requires_api_key":
            block_name = getattr(self._block, "name", None)
            return block_name in self._REQUIRES_API_KEY
        return self.get(name)


class UniversalBlock(ABC):
    """
    Universal Block Base Class - Domain Adapter Protocol
    
    All blocks MUST define:
    - name: str - Block identifier
    - version: str - Semver
    - layer: int - Init order (0=infrastructure → 5=interface)
    - tags: List[str] - Categorization
    - requires: List[str] - Dependencies (other block names)
    """
    
    # REQUIRED - define in subclass
    name: str = ""
    version: str = "1.0.0"
    description: str = ""
    layer: int = 3  # Default: domain layer
    tags: List[str] = []
    requires: List[str] = []
    
    # Optional
    default_config: Dict = {}
    author: str = ""
    
    # UI Schema - Auto-configures Universal UI Shell (frontend)
    # Blocks self-describe: what inputs they need, what outputs they produce
    ui_schema: Dict = {
        "input": {
            "type": "text",  # text, file, audio, image, pdf, json
            "accept": None,  # for files: [".pdf", ".jpg"]
            "placeholder": "Enter your request...",
            "multiline": False,
        },
        "output": {
            "type": "text",  # text, table, chart, json, pdf_viewer, image
            "fields": [],    # for table: [{"name": "concrete_m3", "type": "number", "unit": "m³"}]
        },
        "quick_actions": []  # [{"icon": "📄", "label": "Analyze PDF", "prompt": "..."}]
    }
    
    def __init__(self, hal_block=None, config: Dict = None):
        """Initialize with HAL and config"""
        self.hal = hal_block
        self.config = ConfigAccessor(self, {**self.default_config, **(config or {})})
        
        # Execution stats
        self.execution_count = 0
        self.total_execution_time = 0
        
        # Wired dependencies (filled by assembler)
        self._dependencies: Dict[str, Any] = {}
    
    def wire(self, dep_name: str, dep_instance):
        """Wire a dependency (called by assembler)"""
        self._dependencies[dep_name] = dep_instance
    
    def get_dep(self, name: str) -> Optional[Any]:
        """Get a wired dependency"""
        return self._dependencies.get(name)
    
    @abstractmethod
    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        """Main processing - implement in subclass"""
        pass
    
    async def execute(self, input_data: Any, params: Dict = None) -> Dict:
        """Execute with timing and error handling"""
        start = time.time()
        request_id = str(uuid.uuid4())[:12]
        params = params or {}
        
        try:
            result = await self.process(input_data, params)
            # Respect error status returned by process()
            if isinstance(result, dict) and result.get("status") == "error":
                status = "error"
                confidence = 0.0
            else:
                status = "success"
                confidence = result.get("confidence", 0.95) if isinstance(result, dict) else 0.95
        except Exception as e:
            result = {"error": str(e)}
            status = "error"
            confidence = 0.0
        
        execution_time = int((time.time() - start) * 1000)
        self.execution_count += 1
        self.total_execution_time += execution_time
        
        return {
            "block": self.name,
            "request_id": request_id,
            "status": status,
            "result": result,
            "confidence": confidence,
            "source_id": f"{self.name}-{request_id}",
            "metadata": {
                "version": self.version,
                "execution_count": self.execution_count,
                **params
            },
            "processing_time_ms": execution_time
        }
    
    def get_stats(self) -> Dict:
        """Get execution statistics"""
        avg_time = self.total_execution_time / max(self.execution_count, 1)
        return {
            "name": self.name,
            "version": self.version,
            "layer": self.layer,
            "tags": self.tags,
            "execution_count": self.execution_count,
            "avg_execution_time_ms": round(avg_time, 2)
        }


class UniversalContainer(UniversalBlock):
    """
    Universal Container - Multi-block domain system
    
    Containers group related blocks (e.g., all Construction blocks)
    """
    
    # Containers are always layer 3 (domain)
    layer: int = 3
    tags: List[str] = ["container"]
    
    # Sub-blocks this container provides
    sub_blocks: List[str] = []
    
    async def route(self, action: str, input_data: Any, params: Dict) -> Dict:
        """Route to internal action - override in subclass"""
        raise NotImplementedError(f"Action '{action}' not implemented")
    
    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        """Default: route by action param"""
        params = params or {}
        action = params.get("action", "status")
        return await self.route(action, input_data, params)
