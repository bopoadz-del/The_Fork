"""Container Block - Hyper-Block pattern for nesting blocks

The Container Block is a meta-block that can load and orchestrate other blocks.
It provides:
- Shared event bus for inter-module communication
- Sandbox/security policy enforcement
- Module lifecycle management
- Input/output routing

This enables "blocks within blocks" architecture.
"""

from blocks.base import LegoBlock
from typing import Dict, Any, List, Optional, Type, Callable
import asyncio
from dataclasses import dataclass
from enum import Enum


class SandboxLevel(Enum):
    """Security sandbox levels"""
    NONE = "none"           # No sandboxing
    PERMISSIVE = "permissive"  # Log only
    STRICT = "strict"       # Block dangerous operations
    ISOLATED = "isolated"   # Full isolation (separate process)


@dataclass
class ContainerEvent:
    """Event passed between modules in container"""
    source: str
    event_type: str
    payload: Any
    timestamp: float


class EventBus:
    """Internal pub/sub for container modules"""
    
    def __init__(self):
        self.subscribers: Dict[str, List[Callable]] = {}
        self.history: List[ContainerEvent] = []
        self.max_history = 1000
    
    def subscribe(self, event_type: str, handler: Callable):
        """Subscribe to event type"""
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(handler)
    
    def unsubscribe(self, event_type: str, handler: Callable):
        """Unsubscribe from event type"""
        if event_type in self.subscribers:
            self.subscribers[event_type] = [
                h for h in self.subscribers[event_type] if h != handler
            ]
    
    async def publish(self, event: ContainerEvent):
        """Publish event to all subscribers"""
        # Store in history
        self.history.append(event)
        if len(self.history) > self.max_history:
            self.history.pop(0)
        
        # Notify subscribers
        handlers = self.subscribers.get(event.event_type, [])
        # Also notify wildcards
        handlers.extend(self.subscribers.get("*", []))
        
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    asyncio.create_task(handler(event))
                else:
                    handler(event)
            except Exception as e:
                print(f"Event handler error: {e}")


class SandboxPolicy:
    """Security policy for container modules"""
    
    def __init__(self, level: SandboxLevel = SandboxLevel.STRICT):
        self.level = level
        self.blocked_ops: List[str] = []
        self.allowed_modules: List[str] = []
        
        if level == SandboxLevel.STRICT:
            self.blocked_ops = ["exec", "eval", "__import__", "open", "subprocess"]
    
    def wrap(self, func: Callable) -> Callable:
        """Wrap function with sandbox checks"""
        if self.level == SandboxLevel.NONE:
            return func
        
        async def sandboxed_wrapper(*args, **kwargs):
            # Pre-execution checks
            if self.level == SandboxLevel.STRICT:
                # Check for blocked operations in args
                for arg in args:
                    if isinstance(arg, dict):
                        for blocked in self.blocked_ops:
                            if blocked in str(arg):
                                return {"error": f"Operation '{blocked}' blocked by sandbox"}
            
            # Execute
            return await func(*args, **kwargs)
        
        return sandboxed_wrapper


class ContainerBlock(LegoBlock):
    """
    Container Block - Hyper-Block pattern for nesting blocks
    
    Acts as a micro-kernel that loads and orchestrates child modules.
    Each module is a full LegoBlock that gets:
    - Access to container's event bus
    - Sandboxed execution
    - Shared resources (HAL, config)
    
    Use cases:
    - Dashboard Container: charts, tables, auth widgets
    - Store Container: discovery, reviews, payments
    - Audit Container: logging, compliance, reports
    """
    
    name = "container"
    version = "2.0.0"
    requires = ["config", "memory"]
    layer = 2  # Core layer - orchestrates other blocks
    tags = ["meta", "orchestrator", "container", "core"]
    default_config = {
        "sandbox_level": "strict",
        "max_modules": 20,
        "event_history": 1000,
        "type": "generic"  # dashboard, store, audit, etc.
    }
    
    def __init__(self, hal_block, config: Dict[str, Any]):
        super().__init__(hal_block, config)
        self.modules: Dict[str, LegoBlock] = {}
        self.event_bus = EventBus()
        self.sandbox = SandboxPolicy(SandboxLevel(config.get("sandbox_level", "strict")))
        self.container_type = config.get("type", "generic")
        self.module_configs: Dict[str, Dict] = {}
        
    async def initialize(self) -> bool:
        """Initialize container with event bus"""
        print(f"📦 Container Block initialized")
        print(f"   Type: {self.container_type}")
        print(f"   Sandbox: {self.sandbox.level.value}")
        print(f"   Max modules: {self.config.get('max_modules', 20)}")
        
        # Set up event bus
        self.event_bus.max_history = self.config.get("event_history", 1000)
        
        # Subscribe to all events for logging
        self.event_bus.subscribe("*", self._log_event)
        
        self.initialized = True
        return True
    
    def _log_event(self, event: ContainerEvent):
        """Log all container events"""
        if self.config.get("debug_events"):
            print(f"   📡 [{event.source}] {event.event_type}")
    
    async def load_module(self, module_name: str, module_class: Type[LegoBlock], 
                          module_config: Dict[str, Any] = None) -> LegoBlock:
        """
        Load a module into the container
        
        Args:
            module_name: Unique name for this module instance
            module_class: The LegoBlock class to instantiate
            module_config: Config dict for the module
        
        Returns:
            Initialized module instance
        """
        if module_name in self.modules:
            raise ValueError(f"Module '{module_name}' already loaded")
        
        if len(self.modules) >= self.config.get("max_modules", 20):
            raise ValueError("Maximum number of modules reached")
        
        # Merge configs
        full_config = {**(module_config or {})}
        full_config["_container"] = self.container_type
        full_config["_module_name"] = module_name
        
        # Create instance
        instance = module_class(hal_block=self.hal, config=full_config)
        
        # Inject container services
        instance.container = self
        instance.event_bus = self.event_bus
        instance.module_name = module_name
        
        # Wire dependencies (same as assembler)
        for dep_name in getattr(module_class, 'requires', []):
            if dep_name in self.modules:
                instance.inject(dep_name, self.modules[dep_name])
        
        # Apply sandbox wrapping to execute method
        if self.sandbox.level != SandboxLevel.NONE:
            original_execute = instance.execute
            instance.execute = self.sandbox.wrap(original_execute)
        
        # Initialize
        await instance.initialize()
        
        # Store
        self.modules[module_name] = instance
        self.module_configs[module_name] = full_config
        
        # Publish module loaded event
        await self.event_bus.publish(ContainerEvent(
            source="container",
            event_type="module_loaded",
            payload={"module": module_name, "type": module_class.name},
            timestamp=asyncio.get_event_loop().time()
        ))
        
        print(f"   ✓ Loaded module: {module_name} ({module_class.name})")
        return instance
    
    async def unload_module(self, module_name: str):
        """Unload a module from the container"""
        if module_name not in self.modules:
            return {"error": f"Module '{module_name}' not found"}
        
        module = self.modules.pop(module_name)
        self.module_configs.pop(module_name, None)
        
        await self.event_bus.publish(ContainerEvent(
            source="container",
            event_type="module_unloaded",
            payload={"module": module_name},
            timestamp=asyncio.get_event_loop().time()
        ))
        
        return {"unloaded": module_name}
    
    async def execute(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Route execution to specific module or handle container commands
        
        Input format:
        {
            "action": "module_exec" | "list_modules" | "get_events" | ...,
            "module": "module_name",  # for module_exec
            "payload": {...}  # passed to module
        }
        """
        action = input_data.get("action", "module_exec")
        
        if action == "module_exec":
            return await self._exec_module(input_data)
        
        elif action == "list_modules":
            return {
                "modules": [
                    {
                        "name": name,
                        "type": module.name,
                        "version": module.version,
                        "healthy": module.health().get("healthy", False)
                    }
                    for name, module in self.modules.items()
                ],
                "container_type": self.container_type,
                "sandbox": self.sandbox.level.value
            }
        
        elif action == "get_events":
            event_type = input_data.get("event_type")
            events = self.event_bus.history
            if event_type:
                events = [e for e in events if e.event_type == event_type]
            return {
                "events": [
                    {
                        "source": e.source,
                        "type": e.event_type,
                        "payload": e.payload,
                        "timestamp": e.timestamp
                    }
                    for e in events[-100:]  # Last 100
                ]
            }
        
        elif action == "broadcast":
            # Broadcast event to all modules
            event = ContainerEvent(
                source=input_data.get("source", "external"),
                event_type=input_data.get("event_type", "broadcast"),
                payload=input_data.get("payload", {}),
                timestamp=asyncio.get_event_loop().time()
            )
            await self.event_bus.publish(event)
            return {"broadcast": True, "subscribers": len(self.event_bus.subscribers)}
        
        elif action == "container_info":
            return {
                "name": self.name,
                "version": self.version,
                "type": self.container_type,
                "modules_loaded": len(self.modules),
                "sandbox_level": self.sandbox.level.value,
                "event_subscribers": len(self.event_bus.subscribers),
                "event_history": len(self.event_bus.history)
            }
        
        return {"error": f"Unknown action: {action}"}
    
    async def _exec_module(self, input_data: Dict) -> Dict:
        """Execute a specific module"""
        module_name = input_data.get("module")
        payload = input_data.get("payload", {})
        
        if not module_name:
            return {"error": "No module specified"}
        
        if module_name not in self.modules:
            return {"error": f"Module '{module_name}' not loaded. Loaded: {list(self.modules.keys())}"}
        
        module = self.modules[module_name]
        
        # Add context to payload
        payload["_container_context"] = {
            "module_name": module_name,
            "container_type": self.container_type,
            "timestamp": asyncio.get_event_loop().time()
        }
        
        try:
            result = await module.execute(payload)
            
            # Publish execution event
            await self.event_bus.publish(ContainerEvent(
                source=module_name,
                event_type="execution_complete",
                payload={"action": payload.get("action"), "success": "error" not in result},
                timestamp=asyncio.get_event_loop().time()
            ))
            
            return result
            
        except Exception as e:
            return {"error": f"Module execution failed: {str(e)}", "module": module_name}
    
    def health(self) -> Dict[str, Any]:
        """Container health includes all modules"""
        h = super().health()
        h["container_type"] = self.container_type
        h["modules"] = len(self.modules)
        h["module_health"] = {
            name: module.health()
            for name, module in self.modules.items()
        }
        h["events_tracked"] = len(self.event_bus.history)
        return h


# Pre-configured Container variants for common use cases

class DashboardContainer(ContainerBlock):
    """Pre-configured container for dashboards"""
    name = "dashboard_container"
    default_config = {
        **ContainerBlock.default_config,
        "type": "dashboard",
        "sandbox_level": "permissive"  # Dashboards need more freedom
    }


class StoreContainer(ContainerBlock):
    """Pre-configured container for marketplaces/stores"""
    name = "store_container"
    default_config = {
        **ContainerBlock.default_config,
        "type": "marketplace",
        "sandbox_level": "strict"  # Stores need strict security
    }


class AuditContainer(ContainerBlock):
    """Pre-configured container for compliance/audit"""
    name = "audit_container"
    default_config = {
        **ContainerBlock.default_config,
        "type": "audit",
        "sandbox_level": "strict",
        "immutable": True,  # Audit logs can't be modified
        "event_history": 10000  # Keep more history
    }
