"""Infrastructure Container - Foundation layer container

Contains: HAL, Config, Memory, Database, Migration, Health Check, Secrets
Layer 0 - Must initialize first
"""

from blocks.container.src.block import ContainerBlock


class InfrastructureContainer(ContainerBlock):
    """Foundation layer: HAL, Config, Memory, Database, Migration, Health, Secrets"""
    name = "container_infrastructure"
    version = "1.0.0"
    requires = ["event_bus", "config", "hal", "memory", "database", "migration", "health_check", "secrets"]
    layer = 0
    tags = ["infra", "foundation", "core", "container"]
    
    default_config = {
        "container_type": "infrastructure",
        "isolation_level": "hard",
        "modules": ["hal", "config", "memory", "database", "migration", "health_check", "secrets"],
        "auto_initialize_modules": True
    }
    
    async def initialize(self) -> bool:
        """Initialize infrastructure container and load all modules"""
        print("🏗️  Infrastructure Container initializing...")
        print("   Foundation layer: HAL, Config, Memory, Database, Migration, Health, Secrets")
        
        # Initialize parent
        await super().initialize()
        
        # Load all infrastructure modules
        if self.config.get("auto_initialize_modules"):
            for module_name in self.config["modules"]:
                try:
                    # Import module dynamically
                    module_path = f"blocks.{module_name}.src.block"
                    class_name = f"{module_name.title().replace('_', '')}Block"
                    
                    # Handle special naming cases
                    if module_name == "hal":
                        class_name = "HALBlock"
                    
                    result = await self.execute({
                        "action": "load_module",
                        "module_name": module_name,
                        "module_class": f"{module_path}.{class_name}",
                        "config": self._get_module_config(module_name)
                    })
                    
                    if result.get("error"):
                        print(f"   ⚠️  Failed to load {module_name}: {result['error']}")
                    else:
                        print(f"   ✓ Loaded: {module_name}")
                        
                except Exception as e:
                    print(f"   ⚠️  Error loading {module_name}: {e}")
                    
        print(f"   ✓ Infrastructure Container ready with {len(self.modules)} modules")
        return True
        
    def _get_module_config(self, module_name: str) -> dict:
        """Get module-specific configuration"""
        configs = {
            "database": {
                "connection_pool_size": 10,
                "auto_migrate": False
            },
            "secrets": {
                "encryption": "AES-256",
                "key_rotation_days": 90
            },
            "health_check": {
                "check_interval": 30,
                "deep_check_interval": 300
            },
            "migration": {
                "auto_migrate": False,
                "backup_before_migrate": True
            }
        }
        return configs.get(module_name, {})
