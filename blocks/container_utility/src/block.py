"""Utility Container - Generic tools container

Contains: Voice, Image, Code, Web, Search, Translate, Email, Webhook
Layer 4 - Utility layer
"""

from blocks.container.src.block import ContainerBlock


class UtilityContainer(ContainerBlock):
    """Generic tools: Voice, Image, Code, Web, Search, Translate, Email, Webhook"""
    name = "container_utility"
    version = "1.0.0"
    requires = ["event_bus", "container_infrastructure",
                "voice", "image", "code", "web", "search", "translate", "email", "webhook"]
    layer = 4
    tags = ["utility", "tools", "integration", "container"]
    
    default_config = {
        "container_type": "utility",
        "isolation_level": "soft",
        "modules": ["voice", "image", "code", "web", "search", "translate", "email", "webhook"],
        "auto_initialize_modules": True
    }
    
    async def initialize(self) -> bool:
        """Initialize utility container with all generic tools"""
        print("🛠️  Utility Container initializing...")
        print("   Utility layer: Voice, Image, Code, Web, Search, Translate, Email, Webhook")
        
        # Initialize parent
        await super().initialize()
        
        # Load all utility modules
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
                    
        print(f"   ✓ Utility Container ready with {len(self.modules)} modules")
        return True
        
    def _get_module_config(self, module_name: str) -> dict:
        """Get utility-specific configuration"""
        configs = {
            "voice": {
                "supported_formats": ["mp3", "wav", "ogg"],
                "max_duration": 600  # 10 minutes
            },
            "image": {
                "supported_formats": ["jpg", "png", "gif", "webp"],
                "max_size": 10 * 1024 * 1024  # 10MB
            },
            "code": {
                "supported_languages": ["python", "javascript", "typescript", "bash"],
                "max_execution_time": 30
            },
            "web": {
                "timeout": 30,
                "max_retries": 3,
                "user_agent": "CerebrumBot/1.0"
            },
            "search": {
                "default_provider": "duckduckgo",
                "max_results": 10
            },
            "translate": {
                "default_provider": "libretranslate",
                "cache_results": True
            },
            "email": {
                "provider": "sendgrid",
                "rate_limit": 100  # per hour
            },
            "webhook": {
                "timeout": 30,
                "retry_policy": "exponential",
                "max_retries": 3
            }
        }
        return configs.get(module_name, {})
        
    async def process_multimedia(self, input_data: dict) -> dict:
        """Process multimedia input (voice, image)"""
        media_type = input_data.get("type")
        
        if media_type == "voice":
            return await self.execute({
                "action": "route_to_module",
                "module": "voice",
                "payload": {
                    "action": "transcribe",
                    "audio": input_data.get("audio")
                }
            })
            
        elif media_type == "image":
            return await self.execute({
                "action": "route_to_module",
                "module": "image",
                "payload": {
                    "action": "analyze",
                    "image": input_data.get("image")
                }
            })
            
        return {"error": f"Unsupported media type: {media_type}"}
        
    async def send_notification(self, message: str, channel: str, recipient: str) -> dict:
        """Send notification via email or webhook"""
        if channel == "email":
            return await self.execute({
                "action": "route_to_module",
                "module": "email",
                "payload": {
                    "action": "send",
                    "to": recipient,
                    "body": message
                }
            })
            
        elif channel == "webhook":
            return await self.execute({
                "action": "route_to_module",
                "module": "webhook",
                "payload": {
                    "action": "trigger",
                    "url": recipient,
                    "payload": {"message": message}
                }
            })
            
        return {"error": f"Unsupported channel: {channel}"}
