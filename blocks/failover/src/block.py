"""Failover Block - Unified failover management"""

from blocks.base import LegoBlock
from typing import Dict, Any, List, Callable, Optional
import asyncio
from enum import Enum


class FailoverType(Enum):
    PROVIDER = "provider"  # LLM provider failover (DeepSeek -> Groq -> OpenAI)
    HARDWARE = "hardware"  # Hardware failover (Cloud -> Local -> Cache)
    LOGIC = "logic"        # Logic failover (OCR: Tesseract -> Cloud Vision)


class FailoverBlock(LegoBlock):
    """
    Unified Failover Block
    Handles failover chains for providers, hardware, and logic
    """
    
    name = "failover"
    version = "1.0.0"
    requires = ["config", "monitoring"]
    layer = 2  # Monitoring/resilience layer
    tags = ["resilience", "failover", "core"]
    default_config = {
        "circuit_breaker_threshold": 5,
        "recovery_timeout": 60,
        "health_check_interval": 30
    }
    
    def __init__(self, hal_block, config: Dict[str, Any]):
        super().__init__(hal_block, config)
        self.chains = {}  # block_name -> failover config
        self.executors = {}  # block_name -> {option: executor_func}
        self.monitoring_block = None
    
    async def initialize(self):
        """Initialize failover"""
        print(f"🛡️  Failover Block initialized")
        print(f"   Registered chains: {len(self.chains)}")
        return True
    
    def register_chain(self, block_name: str, chain_config: Dict):
        """
        Register a failover chain for a block
        
        chain_config = {
            "type": FailoverType.PROVIDER,
            "chain": ["deepseek", "groq", "openai"],
            "threshold": 3,  # failures before failover
            "timeout_ms": 8000,
        }
        """
        self.chains[block_name] = chain_config
        self.executors[block_name] = {}
        print(f"   Registered: {block_name} -> {chain_config['chain']}")
    
    def register_executor(self, block_name: str, option: str, executor: Callable):
        """Register an executor function for a chain option"""
        if block_name not in self.executors:
            self.executors[block_name] = {}
        self.executors[block_name][option] = executor
    
    async def execute(self, input_data: Dict) -> Dict:
        """Execute with failover"""
        block_name = input_data.get("block")
        payload = input_data.get("payload", {})
        
        if block_name not in self.chains:
            return {"error": f"No failover chain registered for {block_name}"}
        
        return await self.execute_with_fallback(block_name, payload)
    
    async def execute_with_fallback(self, block_name: str, payload: Dict) -> Dict:
        """Execute with automatic failover"""
        chain_config = self.chains.get(block_name)
        if not chain_config:
            return {"error": f"No chain for {block_name}"}
        
        chain = chain_config["chain"]
        threshold = chain_config.get("threshold", 3)
        timeout_ms = chain_config.get("timeout_ms", 10000)
        
        last_error = None
        
        for option in chain:
            executor = self.executors.get(block_name, {}).get(option)
            if not executor:
                continue
            
            try:
                # Execute with timeout
                result = await asyncio.wait_for(
                    executor(payload),
                    timeout=timeout_ms / 1000
                )
                
                # Record success if monitoring available
                if self.monitoring_block:
                    await self.monitoring_block.execute({
                        "action": "record_call",
                        "provider": option,
                        "latency_ms": 0,  # Would track actual
                        "success": True
                    })
                
                # Add failover metadata
                result["_failover"] = {
                    "used": option,
                    "chain": chain,
                    "attempt": chain.index(option) + 1
                }
                
                return result
                
            except asyncio.TimeoutError:
                last_error = f"{option}: timeout"
            except Exception as e:
                last_error = f"{option}: {str(e)}"
                
                # Record failure
                if self.monitoring_block:
                    await self.monitoring_block.execute({
                        "action": "record_call",
                        "provider": option,
                        "latency_ms": 0,
                        "success": False,
                        "error_type": type(e).__name__
                    })
        
        # All options exhausted
        return {
            "error": "failover_exhausted",
            "chain": chain,
            "last_error": last_error
        }
    
    def health(self) -> Dict[str, Any]:
        """Health check"""
        h = super().health()
        h["chains_registered"] = len(self.chains)
        h["chains"] = {k: v["chain"] for k, v in self.chains.items()}
        return h
