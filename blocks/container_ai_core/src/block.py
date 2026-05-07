"""AI Core Container - AI layer container

Contains: Chat, Vector, Failover, Adaptive Router, Monitoring, Analytics
Layer 2 - Depends on infrastructure and security
"""

from blocks.container.src.block import ContainerBlock


class AICoreContainer(ContainerBlock):
    """AI layer: Chat, Vector, Failover, Adaptive Router, Monitoring, Analytics"""
    name = "container_ai_core"
    version = "1.0.0"
    requires = ["event_bus", "container_infrastructure", "container_security", 
                "chat", "vector", "failover", "adaptive_router", "monitoring", "analytics"]
    layer = 2
    tags = ["ai", "intelligence", "optimization", "container"]
    
    default_config = {
        "container_type": "ai_core",
        "isolation_level": "soft",
        "modules": ["chat", "vector", "failover", "adaptive_router", "monitoring", "analytics"],
        "auto_initialize_modules": True,
        "performance_mode": True
    }
    
    async def initialize(self) -> bool:
        """Initialize AI core container with soft isolation for performance"""
        print("🤖 AI Core Container initializing...")
        print("   AI layer: Chat, Vector, Failover, Adaptive Router, Monitoring, Analytics")
        
        # Initialize parent
        await super().initialize()
        
        # Load all AI modules with performance config
        if self.config.get("auto_initialize_modules"):
            for module_name in self.config["modules"]:
                try:
                    class_name = f"{module_name.title().replace('_', '')}Block"
                    
                    result = await self.execute({
                        "action": "load_module",
                        "module_name": module_name,
                        "module_class": f"blocks.{module_name}.src.block.{class_name}",
                        "config": self._get_module_config(module_name)
                    })
                    
                    if result.get("error"):
                        print(f"   ⚠️  Failed to load {module_name}: {result['error']}")
                    else:
                        print(f"   ✓ Loaded: {module_name}")
                        
                except Exception as e:
                    print(f"   ⚠️  Error loading {module_name}: {e}")
                    
        print(f"   ✓ AI Core Container ready with {len(self.modules)} modules")
        return True
        
    def _get_module_config(self, module_name: str) -> dict:
        """Get performance-optimized configuration"""
        configs = {
            "chat": {
                "performance_mode": True,
                "default_provider": "deepseek",
                "fallback_enabled": True
            },
            "vector": {
                "cache_embeddings": True,
                "batch_size": 100
            },
            "failover": {
                "auto_retry": True,
                "circuit_breaker": True
            },
            "adaptive_router": {
                "learning_enabled": True,
                "exploration_rate": 0.1
            },
            "monitoring": {
                "collect_metrics": True,
                "alert_on_anomaly": True
            },
            "analytics": {
                "real_time": True,
                "prediction_enabled": True
            }
        }
        return configs.get(module_name, {"performance_mode": True})
        
    async def chat(self, message: str, context: dict = None) -> dict:
        """Route chat request through adaptive router"""
        # First select best provider
        router_result = await self.execute({
            "action": "route_to_module",
            "module": "adaptive_router",
            "payload": {
                "action": "select_provider",
                "task": "chat",
                "quality": context.get("quality", "standard") if context else "standard"
            }
        })
        
        provider = router_result.get("selected_provider", "deepseek")
        
        # Execute chat with selected provider
        return await self.execute({
            "action": "route_to_module",
            "module": "chat",
            "payload": {
                "action": "chat",
                "message": message,
                "provider": provider,
                "context": context
            }
        })
        
    async def embed(self, text: str) -> dict:
        """Generate embeddings"""
        return await self.execute({
            "action": "route_to_module",
            "module": "vector",
            "payload": {
                "action": "embed",
                "text": text
            }
        })
