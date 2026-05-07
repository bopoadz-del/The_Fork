"""Team Container - Multi-tenant container

Contains: Team, Auth, Billing, Storage
Layer 3 - Enterprise multi-tenant layer
"""

from blocks.container.src.block import ContainerBlock


class TeamContainer(ContainerBlock):
    """Multi-tenant: Team, Auth, Billing, Storage"""
    name = "container_team"
    version = "1.0.0"
    requires = ["event_bus", "container_infrastructure", "container_security",
                "team", "auth", "billing", "storage"]
    layer = 3
    tags = ["multi-tenant", "teams", "enterprise", "container"]
    
    default_config = {
        "container_type": "team",
        "isolation_level": "hard",  # Data separation
        "modules": ["team", "auth", "billing", "storage"],
        "auto_initialize_modules": True,
        "tenant_isolation": True,
        "max_teams_per_org": 10
    }
    
    async def initialize(self) -> bool:
        """Initialize team container with hard isolation for multi-tenancy"""
        print("👥 Team Container initializing...")
        print("   Team layer: Team, Auth, Billing, Storage")
        print("   Multi-tenant: Hard isolation enabled")
        
        # Initialize parent
        await super().initialize()
        
        # Load all team modules
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
                    
        print(f"   ✓ Team Container ready with {len(self.modules)} modules")
        return True
        
    def _get_module_config(self, module_name: str) -> dict:
        """Get multi-tenant configuration"""
        configs = {
            "team": {
                "tenant_isolation": True,
                "max_teams_per_org": self.config["max_teams_per_org"],
                "max_members_per_team": 50
            },
            "auth": {
                "tenant_isolation": True,
                "enforce_team_context": True
            },
            "billing": {
                "tenant_isolation": True,
                "team_billing": True
            },
            "storage": {
                "tenant_isolation": True,
                "team_scoped": True
            }
        }
        return configs.get(module_name, {"tenant_isolation": True})
        
    async def create_team_workspace(self, team_data: dict) -> dict:
        """Create a new team workspace with all resources"""
        # 1. Create team
        team_result = await self.execute({
            "action": "route_to_module",
            "module": "team",
            "payload": {
                "action": "create_team",
                "name": team_data["name"],
                "owner_id": team_data["owner_id"]
            }
        })
        
        team_id = team_result.get("team_id")
        
        # 2. Setup team storage
        await self.execute({
            "action": "route_to_module",
            "module": "storage",
            "payload": {
                "action": "create_team_bucket",
                "team_id": team_id
            }
        })
        
        # 3. Setup team billing
        await self.execute({
            "action": "route_to_module",
            "module": "billing",
            "payload": {
                "action": "create_team_account",
                "team_id": team_id
            }
        })
        
        return {
            "created": True,
            "team_id": team_id,
            "resources": ["team", "storage", "billing"]
        }
