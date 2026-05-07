#!/usr/bin/env python3
"""
Universal Assembler - Auto-discovers, sorts, and wires any LegoBlock
Drop a new block folder → it's automatically assembled.

v2.0: Now with ContainerBlock support!
"""

import os
import sys
import importlib
import inspect
import asyncio
from pathlib import Path
from typing import Dict, List, Type, Any, Optional, Set
from collections import defaultdict, deque

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent))

from blocks.hal.src.detector import HALBlock


class UniversalAssembler:
    """Auto-discovers and assembles any LegoBlock implementation including Containers"""
    
    # Layer definitions (lower = initialize first)
    LAYERS = {
        0: "infrastructure",  # HAL, Config, Database, Event Bus, Containers
        1: "security",        # Memory, Auth
        2: "monitoring",      # Monitoring, Failover
        3: "core",            # Queue, Storage, Vector
        4: "integration",     # Email, Webhook, Search
        5: "ai",              # Chat, Image, Voice
        6: "domain",          # BIM, PDF, OCR
        7: "utility",         # Code, Translate, Zvec
        99: "unassigned"
    }
    
    def __init__(self, blocks_path: str = "blocks", mode: str = "full"):
        self.blocks_path = Path(blocks_path)
        self.mode = mode
        self.hal = HALBlock()
        self.discovered: Dict[str, Type] = {}
        self.containers: Dict[str, Any] = {}  # Container instances
        self.instances: Dict[str, Any] = {}  # All block instances
        self.dep_graph: Dict[str, Set[str]] = defaultdict(set)
        self.event_bus = None  # Will be set when event_bus is initialized
        
    def discover(self) -> Dict[str, Type]:
        """Auto-discover all LegoBlock classes in blocks/ including Containers"""
        print(f"🔍 Scanning {self.blocks_path}...")
        
        if not self.blocks_path.exists():
            raise FileNotFoundError(f"Blocks path not found: {self.blocks_path}")
        
        # Add project root to path
        sys.path.insert(0, str(self.blocks_path.parent))
        
        found = {}
        containers = []
        
        for block_dir in sorted(self.blocks_path.iterdir()):
            if not block_dir.is_dir():
                continue
            if block_dir.name.startswith('__'):
                continue
                
            block_name = block_dir.name
            src_file = block_dir / "src" / "block.py"
            
            if not src_file.exists():
                continue
            
            try:
                # Import: blocks.{name}.src.block
                module_path = f"blocks.{block_name}.src.block"
                
                # Clear cache for hot-reload support
                if module_path in sys.modules:
                    del sys.modules[module_path]
                    
                module = importlib.import_module(module_path)
                
                # Find LegoBlock subclasses
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    # Skip base classes and non-blocks
                    if name in ["LegoBlock", "ContainerBlock", "BaseBlock"]:
                        continue
                    
                    # Check if it's a valid block (has name or config.name)
                    block_name_attr = getattr(obj, 'name', None)
                    has_config = hasattr(obj, 'config') and hasattr(obj.config, 'name')
                    
                    if block_name_attr or has_config:
                        instance_name = block_name_attr or obj.config.name
                        found[instance_name] = obj
                        
                        # Get layer from either pattern
                        layer = self._get_block_layer(obj)
                        
                        # Track containers
                        if block_name.startswith("container_"):
                            containers.append(instance_name)
                            print(f"   📦 {instance_name} ({obj.__name__}) - CONTAINER (layer {layer})")
                        elif block_name == "event_bus":
                            print(f"   🔌 {instance_name} ({obj.__name__}) - EVENT BUS (layer {layer})")
                        else:
                            print(f"   ✓ {instance_name} ({obj.__name__}) - layer {layer}")
                        
            except Exception as e:
                print(f"   ⚠️  {block_name}: {e}")
                continue
        
        self.discovered = found
        print(f"\n📦 Discovered {len(found)} blocks ({len(containers)} containers)")
        return found
    
    def is_container(self, name: str) -> bool:
        """Check if a block is a container"""
        return name.startswith("container_")
    
    def is_event_bus(self, name: str) -> bool:
        """Check if block is the event bus"""
        return name == "event_bus"
    
    def _get_block_layer(self, block_class) -> int:
        """Get layer from either pattern (class attr or config)"""
        # Try class attribute first (blocks/ style)
        if hasattr(block_class, 'layer'):
            return block_class.layer
        # Try config (app/blocks/ style)
        if hasattr(block_class, 'config') and hasattr(block_class.config, 'layer'):
            return block_class.config.layer
        return 99
    
    def _get_block_requires(self, block_class) -> list:
        """Get requires from either pattern"""
        # Try class attribute first (blocks/ style)
        if hasattr(block_class, 'requires'):
            return block_class.requires
        # Try config (app/blocks/ style)
        if hasattr(block_class, 'config') and hasattr(block_class.config, 'requires'):
            return block_class.config.requires or []
        return []
    
    def _get_block_tags(self, block_class) -> list:
        """Get tags from either pattern"""
        # Try class attribute first (blocks/ style)
        if hasattr(block_class, 'tags'):
            return block_class.tags
        # Try config (app/blocks/ style)
        if hasattr(block_class, 'config') and hasattr(block_class.config, 'tags'):
            return block_class.config.tags or []
        return []
    
    def build_deps(self):
        """Build dependency graph from block.requires"""
        for name, block_class in self.discovered.items():
            self.dep_graph[name] = set(self._get_block_requires(block_class))
        return self.dep_graph
    
    def topological_sort(self) -> List[str]:
        """Sort blocks by dependencies and layer (Kahn's algorithm)"""
        in_degree = defaultdict(int)
        graph = defaultdict(list)
        
        # Build reverse graph
        for block, deps in self.dep_graph.items():
            in_degree[block]  # Ensure exists
            for dep in deps:
                if dep in self.discovered:  # Only track known deps
                    graph[dep].append(block)
                    in_degree[block] += 1
        
        # Start with no-deps nodes, sorted by layer
        # Event bus and infrastructure containers first
        def sort_key(x):
            layer = self._get_block_layer(self.discovered[x])
            # Prioritize event_bus and infrastructure
            if self.is_event_bus(x):
                return (0, x)
            if self.is_container(x) and 'infrastructure' in x:
                return (1, x)
            if self.is_container(x):
                return (2, x)
            return (layer + 3, x)
        
        queue = deque(sorted(
            [n for n in self.discovered if in_degree[n] == 0],
            key=sort_key
        ))
        
        sorted_blocks = []
        
        while queue:
            node = queue.popleft()
            sorted_blocks.append(node)
            
            for dependent in graph[node]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)
            
            # Re-sort queue
            queue = deque(sorted(queue, key=sort_key))
        
        # Check for circular deps
        if len(sorted_blocks) != len(self.discovered):
            missing = set(self.discovered.keys()) - set(sorted_blocks)
            raise ValueError(f"Circular dependency or missing deps: {missing}")
        
        return sorted_blocks
    
    def _build_config(self, name: str, block_class: Type) -> Dict:
        """Build config from env vars and defaults"""
        config = {}
        
        # Class defaults
        if hasattr(block_class, 'default_config'):
            config.update(block_class.default_config)
        
        # Environment: CEREBRUM_{BLOCK}_{KEY}
        prefix = f"CEREBRUM_{name.upper()}_"
        for key, value in os.environ.items():
            if key.startswith(prefix):
                config_key = key[len(prefix):].lower()
                config[config_key] = value
        
        return config
    
    async def assemble(self, target: str = None, dry_run: bool = False) -> Dict[str, Any]:
        """Main assembly pipeline
        
        Args:
            target: Specific block/container to initialize (None = all)
            dry_run: If True, only show what would be done
        """
        profile = self.hal.detect()
        caps = self.hal.get_capabilities()
        
        print(f"\n🔧 Universal Assembler | Mode: {self.mode}")
        if target:
            print(f"   Target: {target}")
        if dry_run:
            print(f"   ⚠️  DRY RUN - No actual initialization")
        print(f"   HAL: {profile.value}")
        print(f"   GPU: {caps.get('has_gpu')}, Memory: {caps.get('memory_gb')}GB")
        
        # 1. Discovery
        self.discover()
        if not self.discovered:
            raise RuntimeError("No blocks discovered!")
        
        # 2. Dependency resolution
        self.build_deps()
        order = self.topological_sort()
        
        # Filter to target if specified
        if target:
            if target not in self.discovered:
                raise ValueError(f"Target '{target}' not found in discovered blocks")
            # Get target and its dependencies
            target_order = []
            target_deps = set()
            
            def add_deps(block_name):
                if block_name in target_deps:
                    return
                target_deps.add(block_name)
                for dep in self.dep_graph.get(block_name, []):
                    if dep in self.discovered:
                        add_deps(dep)
            
            add_deps(target)
            order = [b for b in order if b in target_deps]
        
        print(f"\n📋 Assembly Order:")
        for i, name in enumerate(order, 1):
            deps = self.dep_graph[name]
            dep_str = f" ← {', '.join(deps)}" if deps else ""
            layer = self._get_block_layer(self.discovered[name])
            block_type = "📦" if self.is_container(name) else "🔌" if self.is_event_bus(name) else "📦"
            print(f"   {i}. {block_type} {name} (L{layer}){dep_str}")
        
        if dry_run:
            print("\n✅ Dry run complete - no errors found")
            return {}
        
        # 3. Instantiation & Wiring
        print(f"\n🔌 Initializing...")
        
        for name in order:
            block_class = self.discovered[name]
            config = self._build_config(name, block_class)
            
            # Instantiate
            instance = block_class(hal_block=self.hal, config=config)
            self.instances[name] = instance
            
            # Track containers and event bus separately
            if self.is_container(name):
                self.containers[name] = instance
            if self.is_event_bus(name):
                self.event_bus = instance
            
            # Auto-wire dependencies via inject()
            for dep_name in self.dep_graph[name]:
                if dep_name in self.instances:
                    if hasattr(instance, 'inject'):
                        instance.inject(dep_name, self.instances[dep_name])
                    else:
                        # Fallback: direct attribute setting
                        setattr(instance, f"{dep_name}_block", self.instances[dep_name])
                    print(f"   🔗 {name} → {dep_name}")
            
            # Special: wire event_bus to all containers
            if self.is_event_bus(name) and self.containers:
                for container_name, container in self.containers.items():
                    if hasattr(container, 'event_bus'):
                        container.event_bus = instance
                        print(f"   🔗 {container_name} → event_bus")
            
            # Initialize
            try:
                success = await instance.initialize()
                status = "✅" if success else "⚠️"
            except Exception as e:
                print(f"   ❌ {name}: {e}")
                success = False
                status = "❌"
            
            # Special handling for containers
            if self.is_container(name) and success:
                status = "📦"
                print(f"   {status} {name} (container with {len(getattr(instance, 'modules', {}))} modules)")
            else:
                print(f"   {status} {name}")
            
            # Register containers with event bus
            if self.is_container(name) and self.event_bus and success:
                try:
                    await self.event_bus.execute({
                        "action": "register_container",
                        "container_id": name,
                        "instance": instance,
                        "topics": [f"container.{name}.*"]
                    })
                    print(f"   📡 {name} registered with Event Bus")
                except Exception as e:
                    print(f"   ⚠️  Failed to register {name} with Event Bus: {e}")
        
        # 4. Health check
        await self._health_check()
        
        return self.instances
    
    async def _health_check(self):
        """Health check all blocks including containers"""
        print(f"\n💚 Health Check:")
        healthy = 0
        total_blocks = 0
        total_containers = 0
        
        for name, instance in self.instances.items():
            try:
                h = instance.health()
                ok = h.get('healthy', False)
                status = "🟢" if ok else "🟡"
                if ok:
                    healthy += 1
                
                # Container-specific info
                if self.is_container(name):
                    total_containers += 1
                    modules_loaded = h.get('modules_loaded', '?')
                    status = "📦" if ok else "📦❌"
                    print(f"   {status} {name}: v{h.get('version', '?')} [{modules_loaded} modules]")
                else:
                    total_blocks += 1
                    deps = h.get('dependencies', [])
                    dep_info = f" [{len(deps)} deps]" if deps else ""
                    print(f"   {status} {name}: v{h.get('version', '?')}{dep_info}")
                    
            except Exception as e:
                print(f"   🔴 {name}: {str(e)[:40]}")
        
        print(f"\n✅ Assembled: {total_blocks} blocks | {total_containers} containers | Healthy: {healthy}/{len(self.instances)}")
    
    def get(self, name: str) -> Optional[Any]:
        """Get assembled block or container"""
        return self.instances.get(name)
    
    def get_container(self, name: str) -> Optional[Any]:
        """Get a specific container"""
        return self.containers.get(name)
    
    async def execute(self, block_name: str, input_data: Dict) -> Dict:
        """Execute a block"""
        block = self.get(block_name)
        if not block:
            return {"error": f"Block '{block_name}' not found"}
        return await block.execute(input_data)
    
    async def execute_in_container(self, container_name: str, module_name: str, input_data: Dict) -> Dict:
        """Execute a module inside a container"""
        container = self.get_container(container_name)
        if not container:
            return {"error": f"Container '{container_name}' not found"}
        
        return await container.execute({
            "action": "route_to_module",
            "module": module_name,
            "payload": input_data
        })
    
    def get_topology(self) -> Dict:
        """Get system topology"""
        topology = {
            "blocks": list(self.instances.keys()),
            "containers": list(self.containers.keys()),
            "event_bus_connected": self.event_bus is not None,
            "layers": {}
        }
        
        # Group by layer
        for name, block_class in self.discovered.items():
            layer = getattr(block_class, 'layer', 99)
            if layer not in topology["layers"]:
                topology["layers"][layer] = []
            topology["layers"][layer].append(name)
        
        return topology


async def main():
    """CLI test"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Universal Assembler")
    parser.add_argument("--target", help="Initialize specific block/container")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    parser.add_argument("--topology", action="store_true", help="Show system topology")
    args = parser.parse_args()
    
    print("="*60)
    print("🔥 UNIVERSAL ASSEMBLER v2.0 (with Container support)")
    print("="*60)
    
    assembler = UniversalAssembler(mode="full")
    
    if args.topology:
        # Just show topology without assembling
        assembler.discover()
        topology = assembler.get_topology()
        print("\n🗺️  System Topology:")
        for layer in sorted(topology["layers"].keys()):
            layer_name = assembler.LAYERS.get(layer, f"Layer {layer}")
            blocks = topology["layers"][layer]
            print(f"\n   Layer {layer} ({layer_name}):")
            for b in blocks:
                block_type = "📦" if assembler.is_container(b) else "🔌" if assembler.is_event_bus(b) else "  "
                print(f"      {block_type} {b}")
        return
    
    blocks = await assembler.assemble(target=args.target, dry_run=args.dry_run)
    
    if not blocks:
        return
    
    # Quick tests
    if 'event_bus' in blocks:
        print(f"\n🧪 Testing Event Bus...")
        result = await assembler.execute('event_bus', {'action': 'get_topology'})
        print(f"   Containers: {result.get('containers', [])}")
    
    if 'memory' in blocks:
        print(f"\n🧪 Testing Memory block...")
        result = await assembler.execute('memory', {'action': 'stats'})
        print(f"   Cache stats: {result}")
    
    # Test container if available
    if assembler.containers:
        first_container = list(assembler.containers.keys())[0]
        print(f"\n🧪 Testing Container: {first_container}...")
        result = await assembler.execute(first_container, {'action': 'list_modules'})
        print(f"   Modules: {result}")


if __name__ == "__main__":
    asyncio.run(main())
