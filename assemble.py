#!/usr/bin/env python3
"""
Master Assembly Script - THE GREAT ASSEMBLY

Initializes containers in order:
Infrastructure → Security → Event Bus → AI Core → Construction → Platform

Each container registers itself with the Event Bus on startup.
"""

import asyncio
import sys
import argparse
import logging
from pathlib import Path
from typing import Dict, List, Optional

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('logs/assembly.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Ensure logs directory exists
Path('logs').mkdir(exist_ok=True)

sys.path.insert(0, str(Path(__file__).parent))

from universal_assembler import UniversalAssembler


class MasterAssembler:
    """Master orchestrator for container initialization"""
    
    # Container initialization order (by layer dependency)
    CONTAINER_ORDER = [
        "container_infrastructure",  # L0: Foundation
        "container_security",        # L1: Security
        "event_bus",                 # L0: Nervous system (after infra)
        "container_ai_core",         # L2: AI
        "container_construction",    # L3: Domain
        "container_team",            # L3: Multi-tenant
        "container_platform",        # L3: Product
        "container_store",           # L4: Marketplace
        "container_utility",         # L4: Tools
    ]
    
    def __init__(self):
        self.assembler = UniversalAssembler(mode="production")
        self.initialized: List[str] = []
        self.failed: List[str] = []
        
    async def assemble_layer(self, layer_name: str, dry_run: bool = False) -> bool:
        """Initialize a specific layer"""
        print(f"\n{'='*60}")
        print(f"🔧 ASSEMBLING: {layer_name.upper()}")
        print(f"{'='*60}")
        
        try:
            blocks = await self.assembler.assemble(target=layer_name, dry_run=dry_run)
            
            if layer_name in blocks:
                self.initialized.append(layer_name)
                logger.info(f"✅ {layer_name} initialized successfully")
                return True
            else:
                logger.warning(f"⚠️  {layer_name} not in assembled blocks")
                return False
                
        except Exception as e:
            logger.error(f"❌ {layer_name} failed: {e}")
            self.failed.append(layer_name)
            return False
            
    async def assemble_all(self, dry_run: bool = False, verbose: bool = False) -> Dict:
        """Initialize all containers in order"""
        print("\n" + "="*60)
        print("🏗️  THE GREAT ASSEMBLY - Starting")
        print("="*60)
        
        # First, discover everything
        self.assembler.discover()
        
        # Check which containers are available
        available = set(self.assembler.discovered.keys())
        to_initialize = [c for c in self.CONTAINER_ORDER if c in available]
        
        print(f"\n📋 Assembly Plan ({len(to_initialize)} stages):")
        for i, stage in enumerate(to_initialize, 1):
            deps = self.assembler.dep_graph.get(stage, set())
            dep_str = f" (needs: {', '.join(deps)})" if deps else ""
            print(f"   {i}. {stage}{dep_str}")
        
        if dry_run:
            print("\n⚠️  DRY RUN - No actual initialization")
            return {"initialized": [], "failed": [], "dry_run": True}
        
        # Initialize each layer
        for stage in to_initialize:
            success = await self.assemble_layer(stage, dry_run)
            
            if not success and stage == "container_infrastructure":
                logger.error("💥 Infrastructure failed - stopping assembly")
                break
                
            if verbose and success:
                # Show layer health
                instance = self.assembler.get(stage)
                if instance and hasattr(instance, 'health'):
                    health = instance.health()
                    print(f"\n💚 {stage} Health:")
                    for k, v in health.items():
                        if k != 'healthy':
                            print(f"   {k}: {v}")
        
        # Final summary
        return await self._final_summary()
        
    async def _final_summary(self) -> Dict:
        """Print final assembly summary"""
        print("\n" + "="*60)
        print("📊 ASSEMBLY COMPLETE")
        print("="*60)
        
        total = len(self.initialized) + len(self.failed)
        
        print(f"\n✅ Initialized ({len(self.initialized)}):")
        for name in self.initialized:
            instance = self.assembler.get(name)
            health = instance.health() if instance else {}
            status = "🟢" if health.get('healthy') else "🟡"
            print(f"   {status} {name}")
            
        if self.failed:
            print(f"\n❌ Failed ({len(self.failed)}):")
            for name in self.failed:
                print(f"   🔴 {name}")
                
        # Show topology
        print(f"\n🗺️  System Topology:")
        topology = self.assembler.get_topology()
        print(f"   Total blocks: {len(topology['blocks'])}")
        print(f"   Containers: {len(topology['containers'])}")
        print(f"   Event Bus: {'🟢 Connected' if topology['event_bus_connected'] else '🔴 Not connected'}")
        
        return {
            "initialized": self.initialized,
            "failed": self.failed,
            "success_rate": len(self.initialized) / total if total > 0 else 0,
            "topology": topology
        }
        
    async def test_chain(self, chain: str) -> Dict:
        """Test a cross-container execution chain
        
        Format: "container1.module->container2.module"
        Example: "construction.ocr->ai_core.chat"
        """
        print(f"\n🧪 Testing Chain: {chain}")
        
        steps = chain.split("->")
        results = []
        
        data = {"input": "test"}
        
        for step in steps:
            parts = step.strip().split(".")
            if len(parts) != 2:
                return {"error": f"Invalid step format: {step}"}
                
            container, module = parts
            
            print(f"   Executing: {container}.{module}")
            
            result = await self.assembler.execute_in_container(
                container, module, data
            )
            
            results.append({
                "step": step,
                "result": result
            })
            
            # Pass output to next step
            data = result
            
        return {
            "chain": chain,
            "steps": len(results),
            "results": results
        }


async def main():
    parser = argparse.ArgumentParser(
        description="Master Assembly Script - Initialize Cerebrum System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run to check configuration
  python assemble.py --dry-run
  
  # Initialize infrastructure only
  python assemble.py --target=container_infrastructure
  
  # Full assembly with verbose output
  python assemble.py --full --verbose
  
  # Test a chain
  python assemble.py --test-chain="construction.ocr->ai_core.chat"
  
  # Show topology without initializing
  python assemble.py --topology
        """
    )
    
    parser.add_argument("--target", help="Initialize specific layer/container")
    parser.add_argument("--full", action="store_true", help="Initialize all containers")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--topology", action="store_true", help="Show system topology")
    parser.add_argument("--test-chain", help="Test execution chain")
    
    args = parser.parse_args()
    
    master = MasterAssembler()
    
    if args.topology:
        # Show topology
        master.assembler.discover()
        topology = master.assembler.get_topology()
        
        print("\n🗺️  CEREBRUM SYSTEM TOPOLOGY\n")
        
        for layer in sorted(topology["layers"].keys()):
            layer_name = UniversalAssembler.LAYERS.get(layer, f"Layer {layer}")
            blocks = topology["layers"][layer]
            
            print(f"Layer {layer} - {layer_name}")
            print("-" * 40)
            
            for b in blocks:
                block_type = "📦" if master.assembler.is_container(b) else "🔌" if master.assembler.is_event_bus(b) else "  "
                deps = master.assembler.dep_graph.get(b, set())
                dep_info = f" ← {', '.join(deps)}" if deps else ""
                print(f"  {block_type} {b}{dep_info}")
            print()
            
        print(f"Total: {len(topology['blocks'])} blocks")
        return
        
    if args.test_chain:
        # Run chain test
        result = await master.test_chain(args.test_chain)
        print(f"\n📊 Chain Test Results:")
        print(f"   Chain: {result['chain']}")
        print(f"   Steps: {result['steps']}")
        for step in result.get('results', []):
            print(f"   {step['step']}: {step['result']}")
        return
        
    if args.target:
        # Initialize specific target
        result = await master.assemble_layer(args.target, args.dry_run)
        
        if result and not args.dry_run:
            # Show health
            instance = master.assembler.get(args.target)
            if instance:
                health = instance.health()
                print(f"\n💚 {args.target} Health Check:")
                for k, v in health.items():
                    print(f"   {k}: {v}")
                    
    elif args.full:
        # Full assembly
        result = await master.assemble_all(args.dry_run, args.verbose)
        
        if result.get('failed'):
            sys.exit(1)  # Exit with error if anything failed
            
    else:
        parser.print_help()
        print("\n💡 Try: python assemble.py --dry-run")


if __name__ == "__main__":
    asyncio.run(main())
