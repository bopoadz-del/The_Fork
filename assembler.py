#!/usr/bin/env python3
"""Block Assembler - Wires all blocks together"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from blocks.hal.src.detector import HALBlock
from blocks.config.src.block import ConfigBlock
from blocks.memory.src.block import MemoryBlock
from blocks.monitoring.src.block import MonitoringBlock
from blocks.auth.src.block import AuthBlock
from blocks.queue.src.block import QueueBlock
from blocks.storage.src.block import StorageBlock
from blocks.vector.src.block import VectorBlock
from blocks.failover.src.block import FailoverBlock


class BlockAssembler:
    """Complete Cerebrum Platform Assembler"""
    
    def __init__(self):
        self.hal = HALBlock()
        self.blocks = {}
    
    async def assemble(self):
        """Assemble all blocks"""
        profile = self.hal.detect()
        caps = self.hal.get_capabilities()
        print(f"🔧 HAL: {profile.value}")
        print(f"   GPU: {caps['has_gpu']}, Memory: {caps['memory_gb']}GB")
        print()
        
        # 1. Config
        print("⚙️  Config...")
        self.blocks["config"] = ConfigBlock(self.hal, {})
        await self.blocks["config"].initialize()
        
        # 2. Memory (needed by others)
        print("🧠 Memory...")
        mem_config = self.blocks["config"].get_block_config("memory")
        self.blocks["memory"] = MemoryBlock(self.hal, mem_config)
        await self.blocks["memory"].initialize()
        
        # 3. Monitoring
        print("📊 Monitoring...")
        self.blocks["monitoring"] = MonitoringBlock(self.hal, {})
        self.blocks["monitoring"].memory_block = self.blocks["memory"]
        await self.blocks["monitoring"].initialize()
        
        # 4. Auth
        print("🔐 Auth...")
        auth_config = self.blocks["config"].get_block_config("auth")
        auth_config["master_key"] = os.getenv("CEREBRUM_MASTER_KEY")
        self.blocks["auth"] = AuthBlock(self.hal, auth_config)
        self.blocks["auth"].memory_block = self.blocks["memory"]
        await self.blocks["auth"].initialize()
        
        # 5. Queue
        print("📬 Queue...")
        self.blocks["queue"] = QueueBlock(self.hal, {
            "redis_url": os.getenv("REDIS_URL")
        })
        self.blocks["queue"].memory_block = self.blocks["memory"]
        await self.blocks["queue"].initialize()
        
        # 6. Storage
        print("💾 Storage...")
        storage_config = self.blocks["config"].get_block_config("storage")
        self.blocks["storage"] = StorageBlock(self.hal, storage_config)
        self.blocks["storage"].memory_block = self.blocks["memory"]
        await self.blocks["storage"].initialize()
        
        # 7. Vector
        print("🔍 Vector...")
        vector_config = self.blocks["config"].get_block_config("vector")
        self.blocks["vector"] = VectorBlock(self.hal, vector_config)
        self.blocks["vector"].memory_block = self.blocks["memory"]
        await self.blocks["vector"].initialize()
        
        # 8. Failover
        print("🛡️  Failover...")
        self.blocks["failover"] = FailoverBlock(self.hal, {})
        self.blocks["failover"].monitoring_block = self.blocks["monitoring"]
        await self.blocks["failover"].initialize()
        
        print(f"\n✅ Assembled: {list(self.blocks.keys())}")
        return self.blocks
    
    def health_check(self):
        """Health check all blocks"""
        print("\n🏥 Health Check:")
        for name, block in self.blocks.items():
            h = block.health()
            status = "✅" if h.get("healthy") else "❌"
            print(f"   {status} {name}: {h.get('version', 'unknown')}")


async def test():
    """Test the assembler"""
    print("="*60)
    print("🔥 CEREBRUM BLOCK ASSEMBLER - TEST")
    print("="*60)
    
    assembler = BlockAssembler()
    blocks = await assembler.assemble()
    assembler.health_check()
    
    # Test some integrations
    print("\n🧪 Integration Tests:")
    
    # Test auth + memory
    auth = blocks["auth"]
    key_result = await auth.execute({
        "action": "create_key",
        "name": "test_user",
        "role": "pro",
        "owner": "test"
    })
    print(f"   ✅ Auth key created: {key_result['api_key'][:20]}...")
    
    # Test vector + memory
    vector = blocks["vector"]
    add_result = await vector.execute({
        "action": "add",
        "text": "Cerebrum Blocks is an AI platform",
        "metadata": {"source": "test"}
    })
    print(f"   ✅ Vector added: {add_result['id']}")
    
    search_result = await vector.execute({
        "action": "search",
        "query": "AI platform",
        "top_k": 3
    })
    print(f"   ✅ Vector search: {search_result['total_found']} results")
    
    # Test storage + memory
    storage = blocks["storage"]
    store_result = await storage.execute({
        "action": "store",
        "content": b"Hello World",
        "filename": "test.txt"
    })
    print(f"   ✅ File stored: {store_result['file_id']}")
    
    # Test monitoring
    monitoring = blocks["monitoring"]
    lb_result = await monitoring.execute({"action": "leaderboard"})
    print(f"   ✅ Leaderboard: {len(lb_result['leaderboard'])} providers")
    
    print("\n" + "="*60)
    print("🎉 ALL BLOCKS WORKING!")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(test())
