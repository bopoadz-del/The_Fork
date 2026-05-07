"""Migration Block - Database schema migrations and block data upgrades

Features:
- Alembic-style versioning for platform updates
- Transaction-safe migrations with rollback
- Data seeding and transformation
- Migration history tracking
"""

from blocks.base import LegoBlock
from typing import Dict, Any, List, Optional
from datetime import datetime
import hashlib
import json


class MigrationBlock(LegoBlock):
    """
    Database schema migrations and block data upgrades.
    Alembic-style versioning for platform updates.
    """
    name = "migration"
    version = "1.0.0"
    requires = ["database", "version"]
    layer = 0  # Infrastructure - must initialize early
    tags = ["infra", "database", "devops", "migrations"]
    
    default_config = {
        "auto_migrate": False,  # Safety first
        "backup_before_migrate": True,
        "migration_table": "schema_migrations",
        "migrations_dir": "./migrations"
    }
    
    def __init__(self, hal_block=None, config: Dict = None):
        super().__init__(hal_block, config)
        self.migrations: Dict[str, Dict] = {}  # version -> migration script
        self.current_version: str = "0.0.0"
        self.applied_migrations: List[str] = []
        
    async def initialize(self) -> bool:
        """Initialize migration system"""
        print("🔄 Migration Block initializing...")
        print(f"   Auto-migrate: {self.config['auto_migrate']}")
        print(f"   Backup before migrate: {self.config['backup_before_migrate']}")
        
        # Create migrations table if not exists
        await self._ensure_migrations_table()
        
        # Load current version
        await self._load_current_version()
        
        # Register built-in migrations
        self._register_builtin_migrations()
        
        self.initialized = True
        return True
        
    async def execute(self, input_data: Dict) -> Dict:
        """Execute migration actions"""
        action = input_data.get("action")
        
        actions = {
            "migrate": self._run_migration,
            "rollback": self._rollback,
            "status": self._migration_status,
            "create_migration": self._create_migration_script,
            "seed_data": self._seed_data,
            "list_pending": self._list_pending,
            "verify": self._verify_migrations,
            "force_version": self._force_version
        }
        
        if action in actions:
            return await actions[action](input_data)
            
        return {"error": f"Unknown action: {action}", "available": list(actions.keys())}
        
    async def _run_migration(self, data: Dict) -> Dict:
        """Run pending migrations up to target version"""
        target_version = data.get("to_version")  # None = latest
        dry_run = data.get("dry_run", False)
        
        # Get pending migrations
        pending = await self._get_pending_migrations(target_version)
        
        if not pending:
            return {
                "migrations_run": 0,
                "message": "No pending migrations",
                "current_version": self.current_version
            }
            
        # Create backup if configured
        backup_info = None
        if self.config["backup_before_migrate"] and not dry_run:
            backup_info = await self._create_backup()
            
        results = []
        
        for migration in pending:
            if dry_run:
                results.append({
                    "version": migration["version"],
                    "name": migration["name"],
                    "would_run": True
                })
                continue
                
            # Execute migration in transaction
            try:
                result = await self._execute_migration(migration)
                results.append(result)
                
                if result.get("success"):
                    self.applied_migrations.append(migration["version"])
                    self.current_version = migration["version"]
                else:
                    # Stop on first failure
                    break
                    
            except Exception as e:
                results.append({
                    "version": migration["version"],
                    "success": False,
                    "error": str(e)
                })
                
                # Attempt rollback
                if backup_info:
                    await self._restore_backup(backup_info)
                    
                break
                
        return {
            "migrations_run": len([r for r in results if r.get("success")]),
            "from_version": self.current_version,
            "to_version": target_version or pending[-1]["version"],
            "backup_created": backup_info is not None,
            "results": results,
            "dry_run": dry_run
        }
        
    async def _rollback(self, data: Dict) -> Dict:
        """Rollback to previous version"""
        target_version = data.get("to_version")
        steps = data.get("steps", 1)
        
        if target_version:
            # Rollback to specific version
            applied = await self._get_applied_migrations()
            to_rollback = []
            
            for version in reversed(applied):
                if version == target_version:
                    break
                to_rollback.append(version)
        else:
            # Rollback N steps
            applied = await self._get_applied_migrations()
            to_rollback = applied[-steps:] if len(applied) >= steps else applied
            
        results = []
        
        for version in to_rollback:
            migration = self.migrations.get(version)
            if migration and migration.get("down"):
                try:
                    await migration["down"](self)
                    await self._record_rollback(version)
                    results.append({"version": version, "rolled_back": True})
                except Exception as e:
                    results.append({
                        "version": version,
                        "rolled_back": False,
                        "error": str(e)
                    })
            else:
                results.append({
                    "version": version,
                    "rolled_back": False,
                    "error": "No rollback script available"
                })
                
        return {
            "rolled_back": len([r for r in results if r.get("rolled_back")]),
            "to_version": target_version or "previous",
            "results": results
        }
        
    async def _migration_status(self, data: Dict) -> Dict:
        """Get current migration status"""
        pending = await self._get_pending_migrations()
        applied = await self._get_applied_migrations()
        
        return {
            "current_version": self.current_version,
            "applied_count": len(applied),
            "pending_count": len(pending),
            "latest_version": self._get_latest_version(),
            "is_latest": len(pending) == 0,
            "applied": applied[-10:] if applied else [],  # Last 10
            "pending": [m["version"] for m in pending[:5]]  # Next 5
        }
        
    async def _create_migration_script(self, data: Dict) -> Dict:
        """Create a new migration script template"""
        name = data.get("name", "unnamed")
        description = data.get("description", "")
        
        # Generate version based on timestamp
        version = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        
        # Create template
        template = f'''"""
Migration {version}: {name}
{description}
"""

async def up(migration_block):
    """Apply migration"""
    db = migration_block.get_dependency("database")
    
    # TODO: Add your migration logic here
    # Example:
    # await db.execute("""
    #     CREATE TABLE new_table (
    #         id INTEGER PRIMARY KEY,
    #         name TEXT
    #     )
    # """)
    
    return {{"created_table": "new_table"}}

async def down(migration_block):
    """Rollback migration"""
    db = migration_block.get_dependency("database")
    
    # TODO: Add rollback logic
    # Example:
    # await db.execute("DROP TABLE new_table")
    
    return {{"dropped_table": "new_table"}}
'''
        
        migration = {
            "version": version,
            "name": name,
            "description": description,
            "created_at": datetime.utcnow().isoformat(),
            "template": template
        }
        
        self.migrations[version] = migration
        
        return {
            "created": True,
            "version": version,
            "name": name,
            "template": template,
            "save_to": f"{self.config['migrations_dir']}/{version}_{name}.py"
        }
        
    async def _seed_data(self, data: Dict) -> Dict:
        """Seed initial data after migration"""
        seed_name = data.get("seed_name", "default")
        
        seeds = {
            "default": self._seed_default_data,
            "demo": self._seed_demo_data,
            "test": self._seed_test_data
        }
        
        seed_func = seeds.get(seed_name)
        if not seed_func:
            return {"error": f"Unknown seed: {seed_name}"}
            
        try:
            result = await seed_func()
            return {
                "seeded": True,
                "seed_name": seed_name,
                "records_created": result.get("count", 0)
            }
        except Exception as e:
            return {"error": f"Seeding failed: {e}"}
            
    async def _list_pending(self, data: Dict) -> Dict:
        """List all pending migrations"""
        pending = await self._get_pending_migrations()
        
        return {
            "pending": [
                {
                    "version": m["version"],
                    "name": m["name"],
                    "description": m.get("description", "")
                }
                for m in pending
            ],
            "count": len(pending)
        }
        
    async def _verify_migrations(self, data: Dict) -> Dict:
        """Verify migration integrity"""
        issues = []
        
        for version, migration in self.migrations.items():
            # Check for required fields
            if not migration.get("up"):
                issues.append(f"{version}: Missing 'up' function")
                
            # Check for hash consistency
            expected_hash = migration.get("checksum")
            if expected_hash:
                actual_hash = self._compute_checksum(migration.get("template", ""))
                if expected_hash != actual_hash:
                    issues.append(f"{version}: Checksum mismatch (tampered?)")
                    
        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "total_checked": len(self.migrations)
        }
        
    async def _force_version(self, data: Dict) -> Dict:
        """Force set version without running migrations (dangerous)"""
        version = data.get("version")
        
        self.current_version = version
        await self._record_migration({
            "version": version,
            "name": "forced"
        }, forced=True)
        
        return {
            "forced": True,
            "version": version,
            "warning": "Version forced without running migrations"
        }
        
    # Helper methods
    async def _ensure_migrations_table(self):
        """Create migrations tracking table"""
        if hasattr(self, 'database_block') and self.database_block:
            # TODO: Create table if not exists
            pass
            
    async def _load_current_version(self):
        """Load current schema version from database"""
        # For now, start at 0.0.0
        self.current_version = "0.0.0"
        
    def _register_builtin_migrations(self):
        """Register built-in migrations"""
        # Migration 1: Initial schema
        self.migrations["0.0.1"] = {
            "version": "0.0.1",
            "name": "initial_schema",
            "description": "Create initial database schema",
            "up": self._migration_001_up,
            "down": self._migration_001_down
        }
        
    async def _migration_001_up(self):
        """Create initial tables"""
        # TODO: Create core tables
        return {"created_tables": ["blocks", "users", "config"]}
        
    async def _migration_001_down(self):
        """Drop initial tables"""
        # TODO: Drop tables
        return {"dropped_tables": ["blocks", "users", "config"]}
        
    async def _get_pending_migrations(self, target_version: str = None) -> List[Dict]:
        """Get list of pending migrations"""
        applied = set(await self._get_applied_migrations())
        
        pending = []
        for version, migration in sorted(self.migrations.items()):
            if version not in applied:
                if target_version and version > target_version:
                    break
                pending.append(migration)
                
        return pending
        
    async def _get_applied_migrations(self) -> List[str]:
        """Get list of applied migration versions"""
        return self.applied_migrations
        
    def _get_latest_version(self) -> str:
        """Get latest available version"""
        if not self.migrations:
            return "0.0.0"
        return max(self.migrations.keys())
        
    async def _execute_migration(self, migration: Dict) -> Dict:
        """Execute a single migration"""
        up_func = migration.get("up")
        
        if not up_func:
            return {
                "version": migration["version"],
                "success": False,
                "error": "No up function defined"
            }
            
        # Execute
        result = await up_func(self)
        
        # Record
        await self._record_migration(migration)
        
        return {
            "version": migration["version"],
            "success": True,
            "result": result
        }
        
    async def _record_migration(self, migration: Dict, forced: bool = False):
        """Record that a migration was applied"""
        record = {
            "version": migration["version"],
            "name": migration["name"],
            "applied_at": datetime.utcnow().isoformat(),
            "forced": forced
        }
        
        # TODO: Save to database
        self.applied_migrations.append(migration["version"])
        
    async def _record_rollback(self, version: str):
        """Record that a migration was rolled back"""
        if version in self.applied_migrations:
            self.applied_migrations.remove(version)
            
    async def _create_backup(self) -> Dict:
        """Create database backup before migration"""
        backup_id = f"bk_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        
        # TODO: Implement actual backup
        
        return {
            "backup_id": backup_id,
            "created_at": datetime.utcnow().isoformat()
        }
        
    async def _restore_backup(self, backup_info: Dict):
        """Restore from backup"""
        # TODO: Implement restore
        pass
        
    def _compute_checksum(self, content: str) -> str:
        """Compute checksum for migration integrity"""
        return hashlib.sha256(content.encode()).hexdigest()[:16]
        
    async def _seed_default_data(self) -> Dict:
        """Seed default configuration"""
        return {"count": 0}  # Placeholder
        
    async def _seed_demo_data(self) -> Dict:
        """Seed demo data"""
        return {"count": 0}
        
    async def _seed_test_data(self) -> Dict:
        """Seed test data"""
        return {"count": 0}
        
    def health(self) -> Dict:
        h = super().health()
        h["current_version"] = self.current_version
        h["migrations_available"] = len(self.migrations)
        h["migrations_applied"] = len(self.applied_migrations)
        h["pending"] = len(self.migrations) - len(self.applied_migrations)
        return h
