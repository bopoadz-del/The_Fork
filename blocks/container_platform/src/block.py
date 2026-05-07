"""Platform Container - Cerebrum product container

Contains: Dashboard, Docs, Analytics, Health, Errors, Notifications
Layer 3 - Product layer
"""

from blocks.container.src.block import ContainerBlock


class PlatformContainer(ContainerBlock):
    """Cerebrum product: Dashboard, Docs, Analytics, Health, Errors, Notifications"""
    name = "container_platform"
    version = "1.0.0"
    requires = ["event_bus", "container_infrastructure", "container_security", 
                "container_ai_core", "dashboard", "documentation", "analytics", 
                "health_check", "error_tracking", "notification"]
    layer = 3
    tags = ["platform", "ui", "product", "container"]
    
    default_config = {
        "container_type": "platform",
        "isolation_level": "soft",
        "modules": ["dashboard", "documentation", "analytics", "health_check", "error_tracking", "notification"],
        "auto_initialize_modules": True,
        "ui_enabled": True
    }
    
    async def initialize(self) -> bool:
        """Initialize platform container with UI enabled"""
        print("🖥️  Platform Container initializing...")
        print("   Platform layer: Dashboard, Docs, Analytics, Health, Errors, Notifications")
        
        # Initialize parent
        await super().initialize()
        
        # Load all platform modules
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
                    
        print(f"   ✓ Platform Container ready with {len(self.modules)} modules")
        return True
        
    def _get_module_config(self, module_name: str) -> dict:
        """Get UI-enabled configuration"""
        configs = {
            "dashboard": {
                "ui_enabled": True,
                "default_layout": "grid",
                "theme": "light"
            },
            "documentation": {
                "auto_generate": True,
                "include_playground": True,
                "theme": "github"
            },
            "analytics": {
                "real_time": True,
                "dashboard_widgets": True
            },
            "health_check": {
                "expose_endpoint": True,
                "detailed_status": True
            },
            "error_tracking": {
                "auto_create_issues": True,
                "alert_webhook": True
            },
            "notification": {
                "channels": ["email", "slack", "webhook"],
                "ui_alerts": True
            }
        }
        return configs.get(module_name, {"ui_enabled": True})
        
    async def get_system_dashboard(self) -> dict:
        """Get unified system dashboard data"""
        # Collect data from all modules
        dashboard_data = {}
        
        if "health_check" in self.modules:
            dashboard_data["health"] = await self.execute({
                "action": "route_to_module",
                "module": "health_check",
                "payload": {"action": "deep_check"}
            })
            
        if "analytics" in self.modules:
            dashboard_data["analytics"] = await self.execute({
                "action": "route_to_module",
                "module": "analytics",
                "payload": {"action": "usage_report"}
            })
            
        if "error_tracking" in self.modules:
            dashboard_data["errors"] = await self.execute({
                "action": "route_to_module",
                "module": "error_tracking",
                "payload": {"action": "get_stats"}
            })
            
        return dashboard_data
