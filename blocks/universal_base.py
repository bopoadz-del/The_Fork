"""Universal LegoBlock base with auto-wiring support"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
import time
import uuid


class LegoBlock(ABC):
    """Universal base class for all Cerebrum Blocks.
    
    Features:
    - Auto-discovery via name/requires
    - Dependency injection via inject()
    - Layer-based initialization ordering
    - Tags for filtering
    """
    
    name: str = "base"
    version: str = "1.0.0"
    requires: List[str] = []      # Dependencies auto-wired by assembler
    provides: str = ""            # What this block provides
    layer: int = 99               # 0=infra, 1=security, 2=core, 3=domain, 4=util
    tags: List[str] = []          # For filtering: ["construction", "ai", "storage"]
    
    def __init__(self, hal_block=None, config: Optional[Dict] = None):
        self.hal = hal_block
        self.config = config or {}
        self.execution_count = 0
        self.total_execution_time = 0
        self.initialized = False
        self._dependencies: Dict[str, 'LegoBlock'] = {}
        
    @abstractmethod
    async def execute(self, input_data: Dict) -> Dict:
        """Execute the block with the given input."""
        pass
    
    @abstractmethod
    async def initialize(self) -> bool:
        """Initialize the block."""
        pass
    
    def inject(self, block_name: str, block_instance: 'LegoBlock'):
        """Receive dependency injection from assembler.
        
        Auto-sets attribute like self.memory_block, self.auth_block
        """
        self._dependencies[block_name] = block_instance
        # Set as attribute (e.g., self.memory_block)
        attr_name = f"{block_name}_block"
        setattr(self, attr_name, block_instance)
        
    def get_dependency(self, name: str) -> Optional['LegoBlock']:
        """Get injected dependency by name."""
        return self._dependencies.get(name)
        
    def health(self) -> Dict[str, Any]:
        """Return health status."""
        return {
            "name": self.name,
            "version": self.version,
            "initialized": self.initialized,
            "execution_count": self.execution_count,
            "healthy": True,
            "dependencies": list(self._dependencies.keys())
        }
        
    async def timed_execute(self, input_data: Dict) -> Dict:
        """Execute with timing and error handling."""
        start = time.time()
        try:
            result = await self.execute(input_data)
            self.execution_count += 1
            return result
        except Exception as e:
            return {"error": str(e), "block": self.name}
        finally:
            self.total_execution_time += time.time() - start
