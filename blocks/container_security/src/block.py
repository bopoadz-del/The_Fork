"""Security Container - Security layer container

Contains: Auth, Secrets, Sandbox, Audit, Rate Limiter
Layer 1 - Depends on infrastructure
"""

from blocks.container.src.block import ContainerBlock


class SecurityContainer(ContainerBlock):
    """Security layer: Auth, Secrets, Sandbox, Audit, Rate Limiter"""
    name = "container_security"
    version = "1.0.0"
    requires = ["event_bus", "container_infrastructure", "auth", "secrets", "sandbox", "audit", "rate_limiter"]
    layer = 1
    tags = ["security", "isolation", "compliance", "container"]
    
    default_config = {
        "container_type": "security",
        "isolation_level": "strict",
        "modules": ["auth", "secrets", "sandbox", "audit", "rate_limiter"],
        "auto_initialize_modules": True,
        "enforce_policies": True
    }
    
    async def initialize(self) -> bool:
        """Initialize security container with strict isolation"""
        print("🔒 Security Container initializing...")
        print("   Security layer: Auth, Secrets, Sandbox, Audit, Rate Limiter")
        
        # Initialize parent
        await super().initialize()
        
        # Load all security modules with strict config
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
                    
        print(f"   ✓ Security Container ready with {len(self.modules)} modules")
        return True
        
    def _get_module_config(self, module_name: str) -> dict:
        """Get strict security configuration for modules"""
        configs = {
            "auth": {
                "enforce_mfa": True,
                "session_timeout": 3600,
                "max_login_attempts": 5
            },
            "secrets": {
                "encryption": "AES-256-GCM",
                "auto_rotate": True,
                "audit_access": True
            },
            "sandbox": {
                "isolation": "strict",
                "resource_limits": True,
                "network_access": False
            },
            "audit": {
                "immutable_logs": True,
                "hash_chain": True,
                "retention_days": 365
            },
            "rate_limiter": {
                "strategy": "sliding_window",
                "default_limit": 1000,
                "burst_allowance": 10
            }
        }
        return configs.get(module_name, {"isolation": "strict"})
        
    async def authenticate_request(self, request: dict) -> dict:
        """Authenticate a request using the auth module"""
        if "auth" not in self.modules:
            return {"error": "Auth module not loaded"}
            
        return await self.execute({
            "action": "route_to_module",
            "module": "auth",
            "payload": {
                "action": "validate_key",
                "api_key": request.get("api_key")
            }
        })
        
    async def check_rate_limit(self, key: str, resource: str) -> dict:
        """Check rate limit for a key"""
        if "rate_limiter" not in self.modules:
            return {"allowed": True}  # Default allow if not loaded
            
        return await self.execute({
            "action": "route_to_module",
            "module": "rate_limiter",
            "payload": {
                "action": "check_limit",
                "key": key,
                "resource": resource
            }
        })
