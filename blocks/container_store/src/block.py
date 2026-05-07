"""Store Container - Marketplace container

Contains: Discovery, Review, Payment Split, Version, Validation, Billing
Layer 4 - Marketplace layer
"""

from blocks.container.src.block import ContainerBlock


class StoreContainer(ContainerBlock):
    """Marketplace: Discovery, Review, Payment Split, Version, Validation, Billing"""
    name = "container_store"
    version = "1.0.0"
    requires = ["event_bus", "container_infrastructure", "container_security", 
                "container_ai_core", "discovery", "review", "payment_split", 
                "version", "validation", "billing"]
    layer = 4
    tags = ["marketplace", "store", "economy", "container"]
    
    default_config = {
        "container_type": "store",
        "isolation_level": "strict",  # 3rd party code isolation
        "modules": ["discovery", "review", "payment_split", "version", "validation", "billing"],
        "auto_initialize_modules": True,
        "marketplace_mode": True,
        "platform_fee_percent": 20
    }
    
    async def initialize(self) -> bool:
        """Initialize store container with strict isolation for marketplace"""
        print("🏪 Store Container initializing...")
        print("   Marketplace layer: Discovery, Review, Payment Split, Version, Validation, Billing")
        print(f"   Platform fee: {self.config['platform_fee_percent']}%")
        
        # Initialize parent
        await super().initialize()
        
        # Load all store modules
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
                    
        print(f"   ✓ Store Container ready with {len(self.modules)} modules")
        return True
        
    def _get_module_config(self, module_name: str) -> dict:
        """Get marketplace-specific configuration"""
        configs = {
            "discovery": {
                "marketplace_mode": True,
                "boost_rated": True,
                "max_suggestions": 5
            },
            "review": {
                "require_verified": True,
                "auto_moderate": True,
                "trusted_reviewer_threshold": 10
            },
            "payment_split": {
                "marketplace_mode": True,
                "platform_fee_percent": self.config["platform_fee_percent"],
                "referral_bonus_percent": 5
            },
            "version": {
                "auto_update_patch": False,  # User consent required
                "breaking_change_threshold_days": 30
            },
            "validation": {
                "auto_certify_threshold": 0.9,
                "security_checks": ["network", "filesystem", "memory"]
            },
            "billing": {
                "marketplace_mode": True,
                "payout_schedule": "monthly"
            }
        }
        return configs.get(module_name, {"marketplace_mode": True})
        
    async def publish_block(self, block_data: dict, creator_id: str) -> dict:
        """Publish a new block to the marketplace"""
        # 1. Validate the block
        validation_result = await self.execute({
            "action": "route_to_module",
            "module": "validation",
            "payload": {
                "action": "validate_block",
                "block_id": block_data["name"],
                "code": block_data.get("code")
            }
        })
        
        if not validation_result.get("passed"):
            return {
                "published": False,
                "error": "Validation failed",
                "details": validation_result.get("details")
            }
            
        # 2. Create version
        version_result = await self.execute({
            "action": "route_to_module",
            "module": "version",
            "payload": {
                "action": "publish_version",
                "block_id": block_data["name"],
                "version": block_data.get("version", "1.0.0")
            }
        })
        
        # 3. Index for discovery
        await self.execute({
            "action": "route_to_module",
            "module": "discovery",
            "payload": {
                "action": "index_block",
                "profile": block_data
            }
        })
        
        return {
            "published": True,
            "block_id": block_data["name"],
            "validated": True,
            "certified": validation_result.get("certified", False)
        }
