#!/usr/bin/env python3
"""
Cerebrum PoC Assembler - Cloud/Internal Memory Only (No Jetson)
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from blocks.hal.src.detector import HALBlock
from blocks.config.src.block import ConfigBlock
from blocks.vector.src.block import VectorBlock
from blocks.storage.src.block import StorageBlock
from blocks.memory.src.block import MemoryBlock
from blocks.queue.src.block import QueueBlock
from blocks.monitoring.src.block import MonitoringBlock
from blocks.auth.src.block import AuthBlock
from blocks.failover.src.block import FailoverBlock
from blocks.bim.src.block import BIMBlock

# PoC Mode - Force Cloud Profile
os.environ["HAL_PROFILE"] = "cloud_render"

class PoCAssembler:
    """Assemble for Cloud PoC - Google Drive + Internal Memory"""
    
    def __init__(self):
        self.hal = HALBlock()
        self.blocks = {}
    
    async def assemble(self):
        """Build system for cloud PoC"""
        profile = self.hal.detect()
        print(f"☁️  CLOUD PoC Mode: {profile.value}\n")
        
        # 1. Memory (Internal cache - PRIMARY storage for PoC)
        print("🧠 Internal Memory Cache...")
        self.blocks["memory"] = MemoryBlock(self.hal, {
            "max_size": 100000,  # 100k items for PoC
            "default_ttl": 86400  # 24 hours
        })
        await self.blocks["memory"].initialize()
        
        # 2. Config
        print("⚙️  Config...")
        self.blocks["config"] = ConfigBlock(self.hal, {})
        await self.blocks["config"].initialize()
        
        # 3. Queue (memory-backed for PoC)
        print("📬 Queue (Memory Mode)...")
        self.blocks["queue"] = QueueBlock(self.hal, {
            "redis_url": None  # Use memory instead of Redis
        })
        await self.blocks["queue"].initialize()
        
        # 4. Storage (Google Drive + Local)
        print("💾 Storage (Drive + Local)...")
        self.blocks["storage"] = StorageBlock(self.hal, {
            "backend": "local",
            "data_dir": "./data/storage"
        })
        self.blocks["storage"].memory_block = self.blocks["memory"]
        await self.blocks["storage"].initialize()
        
        # 5. Vector (ChromaDB in memory)
        print("🔍 Vector (Chroma In-Memory)...")
        self.blocks["vector"] = VectorBlock(self.hal, {
            "backend": "chroma",
            "embedding_model": "all-MiniLM-L6-v2"
        })
        self.blocks["vector"].memory_block = self.blocks["memory"]
        await self.blocks["vector"].initialize()
        
        # 6. Auth
        print("🔐 Auth...")
        self.blocks["auth"] = AuthBlock(self.hal, {"master_key": "poc_master_key"})
        self.blocks["auth"].memory_block = self.blocks["memory"]
        await self.blocks["auth"].initialize()
        
        # 7. Monitoring
        print("📊 Monitoring...")
        self.blocks["monitoring"] = MonitoringBlock(self.hal, {})
        self.blocks["monitoring"].memory_block = self.blocks["memory"]
        await self.blocks["monitoring"].initialize()
        
        # 8. Failover
        print("🛡️  Failover...")
        self.blocks["failover"] = FailoverBlock(self.hal, {})
        self.blocks["failover"].monitoring_block = self.blocks["monitoring"]
        await self.blocks["failover"].initialize()
        
        # 9. BIM (File Processor - Cloud PoC)
        print("\n📐 BIM File Processor...")
        self.blocks["bim"] = BIMBlock(self.hal, {})
        self.blocks["bim"].storage_block = self.blocks["storage"]
        self.blocks["bim"].vector_block = self.blocks["vector"]
        await self.blocks["bim"].initialize()
        
        print(f"\n✅ PoC Assembled: {list(self.blocks.keys())}")
        print(f"   Storage: Internal Memory + Local Drive")
        print(f"   BIM: File-based (PDF, IFC, DWG)")
        print(f"   No Jetson - Cloud only")
        
        return self.blocks
    
    async def demo_bim(self):
        """Demo BIM file processing"""
        print("\n📐 BIM Demo: Creating test project...")
        
        bim = self.blocks["bim"]
        storage = self.blocks["storage"]
        
        # Create test file
        await storage.execute({
            "action": "store",
            "content": b"Test BIM drawing content",
            "filename": "drawing_a101.pdf",
            "metadata": {"type": "drawing", "sheet": "A101"}
        })
        
        print("   Test file stored")
        
        # Note: Full demo needs actual file structure
        print("   (Full indexing requires actual folder)")


async def test():
    """Test the PoC assembler"""
    print("="*60)
    print("☁️  CEREBRUM PoC ASSEMBLER - CLOUD MODE")
    print("="*60)
    
    assembler = PoCAssembler()
    blocks = await assembler.assemble()
    
    # Health check
    print("\n🏥 Health Check:")
    for name, block in blocks.items():
        h = block.health()
        status = "✅" if h.get("healthy") else "❌"
        print(f"   {status} {name}: {h.get('version', 'unknown')}")
    
    print("\n" + "="*60)
    print("🎉 PoC SYSTEM READY!")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(test())
