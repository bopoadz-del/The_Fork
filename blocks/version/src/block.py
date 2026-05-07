"""Version Block - Semantic versioning for blocks

Handles versioning, dependency management, breaking changes,
rollbacks, and migration paths for block updates.
"""

from blocks.base import LegoBlock
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import re
from packaging import version as pkg_version


class VersionBlock(LegoBlock):
    """
    Semantic versioning for blocks.
    Handles updates, breaking changes, rollback, dependencies.
    """
    name = "version"
    version = "1.0.0"
    requires = ["database", "storage"]
    layer = 3
    tags = ["platform", "devops", "store", "versioning"]
    
    default_config = {
        "default_strategy": "semver",  # semantic versioning
        "auto_update_patch": True,
        "breaking_change_threshold_days": 30,  # Notice period
        "max_versions_kept": 10,
        "require_changelog": True
    }
    
    def __init__(self, hal_block=None, config: Dict = None):
        super().__init__(hal_block, config)
        self.versions: Dict[str, List[Dict]] = {}  # block_id -> versions
        self.dependencies: Dict[str, Dict] = {}  # block_id -> {dep_name: constraint}
        self.changelogs: Dict[str, List[Dict]] = {}  # version_id -> changelog entries
        self.deprecated: Dict[str, Dict] = {}  # version_id -> deprecation info
        
    async def initialize(self) -> bool:
        """Initialize version management"""
        print("🏷️  Version Block initializing...")
        print(f"   Strategy: {self.config['default_strategy']}")
        print(f"   Auto-update patch: {self.config['auto_update_patch']}")
        
        # TODO: Create database tables
        # - versions: block_id, version, created_at, status, breaking_changes
        # - dependencies: block_id, version, dep_name, dep_constraint
        # - changelogs: version_id, change_type, description
        # - migrations: from_version, to_version, migration_script
        
        self.initialized = True
        return True
        
    async def execute(self, input_data: Dict) -> Dict:
        """Execute version management actions"""
        action = input_data.get("action")
        
        actions = {
            "publish_version": self._publish_version,
            "check_compatibility": self._check_compatibility,
            "rollback": self._rollback_version,
            "deprecate": self._deprecate_version,
            "dependency_tree": self._dependency_tree,
            "get_version": self._get_version,
            "list_versions": self._list_versions,
            "compare_versions": self._compare_versions,
            "get_changelog": self._get_changelog,
            "suggest_update": self._suggest_update,
            "validate_version": self._validate_version,
            "yank_version": self._yank_version
        }
        
        if action in actions:
            return await actions[action](input_data)
            
        return {"error": f"Unknown action: {action}", "available": list(actions.keys())}
        
    async def _publish_version(self, data: Dict) -> Dict:
        """Publish a new version of a block"""
        block_id = data.get("block_id")
        new_version = data.get("version")  # "1.2.3"
        changelog = data.get("changelog", [])
        dependencies = data.get("dependencies", {})  # {"other_block": ">=1.0.0"}
        breaking_changes = data.get("breaking_changes", [])
        migration_guide = data.get("migration_guide", "")
        
        if not block_id or not new_version:
            return {"error": "block_id and version required"}
            
        # Validate version format
        if not self._is_valid_semver(new_version):
            return {"error": f"Invalid semantic version: {new_version}"}
            
        # Check if version already exists
        existing = self.versions.get(block_id, [])
        if any(v["version"] == new_version for v in existing):
            return {"error": f"Version {new_version} already exists for {block_id}"}
            
        # Validate version progression
        if existing:
            latest = max(existing, key=lambda x: pkg_version.parse(x["version"]))
            if pkg_version.parse(new_version) <= pkg_version.parse(latest["version"]):
                return {
                    "error": f"New version must be greater than latest ({latest['version']})"
                }
                
        # Require changelog for minor/major updates
        version_type = self._get_version_type(new_version, latest["version"] if existing else None)
        if self.config["require_changelog"] and version_type in ["minor", "major"] and not changelog:
            return {"error": "Changelog required for minor/major versions"}
            
        # Create version record
        version_record = {
            "block_id": block_id,
            "version": new_version,
            "version_type": version_type,
            "created_at": datetime.utcnow().isoformat(),
            "status": "active",
            "dependencies": dependencies,
            "breaking_changes": breaking_changes,
            "migration_guide": migration_guide,
            "downloads": 0,
            "yanked": False
        }
        
        if block_id not in self.versions:
            self.versions[block_id] = []
        self.versions[block_id].append(version_record)
        
        # Store changelog
        version_key = f"{block_id}@{new_version}"
        self.changelogs[version_key] = changelog
        
        # Store dependencies
        if dependencies:
            self.dependencies[version_key] = dependencies
            
        # Clean old versions
        await self._cleanup_old_versions(block_id)
        
        print(f"   ✓ Published {block_id}@{new_version} ({version_type})")
        
        return {
            "published": True,
            "block_id": block_id,
            "version": new_version,
            "type": version_type,
            "has_breaking_changes": len(breaking_changes) > 0
        }
        
    async def _check_compatibility(self, data: Dict) -> Dict:
        """Check if new version is compatible with current stack"""
        block_id = data.get("block_name")
        new_version = data.get("new_version")
        current_stack = data.get("current_stack", [])  # [{"name": "x", "version": "1.0.0"}]
        
        if block_id not in self.versions:
            return {"error": f"Block '{block_id}' not found"}
            
        version_key = f"{block_id}@{new_version}"
        version_info = self._get_version_info(block_id, new_version)
        
        if not version_info:
            return {"error": f"Version {new_version} not found for {block_id}"}
            
        # Check breaking changes
        breaking = version_info.get("breaking_changes", [])
        
        # Check dependency conflicts
        deps = self.dependencies.get(version_key, {})
        conflicts = []
        
        for dep_name, constraint in deps.items():
            # Find in current stack
            installed = next(
                (s for s in current_stack if s.get("name") == dep_name),
                None
            )
            if installed:
                if not self._satisfies_constraint(installed["version"], constraint):
                    conflicts.append({
                        "dependency": dep_name,
                        "required": constraint,
                        "installed": installed["version"]
                    })
            else:
                conflicts.append({
                    "dependency": dep_name,
                    "required": constraint,
                    "installed": "not installed"
                })
                
        # Check reverse dependencies (what depends on this block)
        reverse_deps = []
        for v_key, deps in self.dependencies.items():
            if block_id in deps:
                constraint = deps[block_id]
                if not self._satisfies_constraint(new_version, constraint):
                    reverse_deps.append(v_key)
                    
        # Migration required?
        migration_required = len(breaking) > 0 or len(conflicts) > 0
        
        return {
            "block_id": block_id,
            "new_version": new_version,
            "compatible": not migration_required,
            "migration_required": migration_required,
            "breaking_changes": breaking,
            "dependency_conflicts": conflicts,
            "affected_by_update": reverse_deps,
            "migration_guide": version_info.get("migration_guide", "")
        }
        
    async def _rollback_version(self, data: Dict) -> Dict:
        """Rollback to a previous version"""
        block_id = data.get("block_id")
        target_version = data.get("target_version")
        
        if block_id not in self.versions:
            return {"error": "Block not found"}
            
        target = self._get_version_info(block_id, target_version)
        if not target:
            return {"error": f"Version {target_version} not found"}
            
        # Mark current latest as rolled back
        versions = self.versions[block_id]
        current_latest = max(versions, key=lambda x: pkg_version.parse(x["version"]))
        
        if current_latest["version"] == target_version:
            return {"error": "Already on target version"}
            
        current_latest["status"] = "rolled_back"
        
        # TODO: Trigger rollback in deployment
        
        return {
            "rolled_back": True,
            "block_id": block_id,
            "from_version": current_latest["version"],
            "to_version": target_version,
            "rolled_back_at": datetime.utcnow().isoformat()
        }
        
    async def _deprecate_version(self, data: Dict) -> Dict:
        """Deprecate a version (scheduled for removal)"""
        block_id = data.get("block_id")
        version = data.get("version")
        reason = data.get("reason", "")
        replacement = data.get("replacement_version")
        removal_date = data.get("removal_date")
        
        version_key = f"{block_id}@{version}"
        
        self.deprecated[version_key] = {
            "block_id": block_id,
            "version": version,
            "deprecated_at": datetime.utcnow().isoformat(),
            "reason": reason,
            "replacement": replacement,
            "scheduled_removal": removal_date or (
                datetime.utcnow() + timedelta(
                    days=self.config["breaking_change_threshold_days"]
                )
            ).isoformat()
        }
        
        # Mark in versions
        v_info = self._get_version_info(block_id, version)
        if v_info:
            v_info["status"] = "deprecated"
            
        return {
            "deprecated": True,
            "block_id": block_id,
            "version": version,
            "scheduled_removal": self.deprecated[version_key]["scheduled_removal"]
        }
        
    async def _dependency_tree(self, data: Dict) -> Dict:
        """Get full dependency tree for a block version"""
        block_id = data.get("block_id")
        version = data.get("version")
        
        version_key = f"{block_id}@{version}"
        
        tree = await self._build_dependency_tree(version_key, visited=set())
        
        return {
            "block_id": block_id,
            "version": version,
            "dependency_tree": tree,
            "total_dependencies": self._count_deps(tree)
        }
        
    async def _get_version(self, data: Dict) -> Dict:
        """Get specific version info"""
        block_id = data.get("block_id")
        version = data.get("version")
        
        v_info = self._get_version_info(block_id, version)
        if not v_info:
            return {"error": "Version not found"}
            
        version_key = f"{block_id}@{version}"
        
        return {
            "version": v_info,
            "changelog": self.changelogs.get(version_key, []),
            "dependencies": self.dependencies.get(version_key, {}),
            "deprecated": version_key in self.deprecated
        }
        
    async def _list_versions(self, data: Dict) -> Dict:
        """List all versions of a block"""
        block_id = data.get("block_id")
        include_yanked = data.get("include_yanked", False)
        
        if block_id not in self.versions:
            return {"error": "Block not found"}
            
        versions = self.versions[block_id]
        
        if not include_yanked:
            versions = [v for v in versions if not v.get("yanked")]
            
        # Sort by version
        versions = sorted(versions, key=lambda x: pkg_version.parse(x["version"]), reverse=True)
        
        return {
            "block_id": block_id,
            "versions": [
                {
                    "version": v["version"],
                    "type": v["version_type"],
                    "status": v["status"],
                    "created_at": v["created_at"],
                    "breaking_changes": len(v.get("breaking_changes", [])) > 0
                }
                for v in versions
            ],
            "latest": versions[0]["version"] if versions else None
        }
        
    async def _compare_versions(self, data: Dict) -> Dict:
        """Compare two versions"""
        block_id = data.get("block_id")
        from_version = data.get("from_version")
        to_version = data.get("to_version")
        
        from_info = self._get_version_info(block_id, from_version)
        to_info = self._get_version_info(block_id, to_version)
        
        if not from_info or not to_info:
            return {"error": "One or both versions not found"}
            
        # Get changelog between versions
        all_versions = self.versions.get(block_id, [])
        version_range = [
            v for v in all_versions
            if (pkg_version.parse(from_version) < pkg_version.parse(v["version"]) <=
                pkg_version.parse(to_version))
        ]
        
        combined_changes = []
        breaking = []
        
        for v in version_range:
            v_key = f"{block_id}@{v['version']}"
            combined_changes.extend(self.changelogs.get(v_key, []))
            breaking.extend(v.get("breaking_changes", []))
            
        return {
            "from": from_version,
            "to": to_version,
            "upgrade_type": self._get_version_type(to_version, from_version),
            "versions_between": len(version_range),
            "changes": combined_changes,
            "breaking_changes": breaking,
            "safe_upgrade": len(breaking) == 0
        }
        
    async def _get_changelog(self, data: Dict) -> Dict:
        """Get changelog for a version"""
        block_id = data.get("block_id")
        version = data.get("version")
        
        version_key = f"{block_id}@{version}"
        
        return {
            "block_id": block_id,
            "version": version,
            "changelog": self.changelogs.get(version_key, [])
        }
        
    async def _suggest_update(self, data: Dict) -> Dict:
        """Suggest update path for current stack"""
        current_stack = data.get("current_stack", [])  # [{"name": "x", "version": "1.0.0"}]
        
        suggestions = []
        
        for item in current_stack:
            block_id = item["name"]
            current_version = item["version"]
            
            if block_id not in self.versions:
                continue
                
            versions = self.versions[block_id]
            latest = max(versions, key=lambda x: pkg_version.parse(x["version"]))
            
            if pkg_version.parse(latest["version"]) > pkg_version.parse(current_version):
                update_type = self._get_version_type(latest["version"], current_version)
                
                suggestions.append({
                    "block_id": block_id,
                    "current": current_version,
                    "latest": latest["version"],
                    "update_type": update_type,
                    "safe_auto_update": update_type == "patch" and self.config["auto_update_patch"],
                    "breaking_changes": len(latest.get("breaking_changes", [])) > 0
                })
                
        # Sort by safety (patch first, then minor, then major)
        type_order = {"patch": 0, "minor": 1, "major": 2}
        suggestions.sort(key=lambda x: type_order.get(x["update_type"], 3))
        
        return {
            "suggestions": suggestions,
            "safe_updates": [s for s in suggestions if s["safe_auto_update"]],
            "require_attention": [s for s in suggestions if not s["safe_auto_update"]]
        }
        
    async def _validate_version(self, data: Dict) -> Dict:
        """Validate a version string"""
        version = data.get("version")
        
        is_valid = self._is_valid_semver(version)
        
        if is_valid:
            parsed = pkg_version.parse(version)
            return {
                "valid": True,
                "version": version,
                "major": parsed.major,
                "minor": parsed.minor,
                "micro": parsed.micro
            }
        else:
            return {
                "valid": False,
                "version": version,
                "error": "Invalid semantic version. Format: X.Y.Z"
            }
            
    async def _yank_version(self, data: Dict) -> Dict:
        """Yank a version (emergency removal)"""
        block_id = data.get("block_id")
        version = data.get("version")
        reason = data.get("reason", "")
        
        v_info = self._get_version_info(block_id, version)
        if not v_info:
            return {"error": "Version not found"}
            
        v_info["yanked"] = True
        v_info["yanked_at"] = datetime.utcnow().isoformat()
        v_info["yank_reason"] = reason
        v_info["status"] = "yanked"
        
        return {
            "yanked": True,
            "block_id": block_id,
            "version": version,
            "reason": reason
        }
        
    # Helper methods
    def _is_valid_semver(self, version: str) -> bool:
        """Check if version follows semantic versioning"""
        pattern = r'^\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?(?:\+[a-zA-Z0-9.-]+)?$'
        return bool(re.match(pattern, version))
        
    def _get_version_type(self, new_version: str, old_version: Optional[str]) -> str:
        """Determine if update is patch, minor, or major"""
        if not old_version:
            return "initial"
            
        new = pkg_version.parse(new_version)
        old = pkg_version.parse(old_version)
        
        if new.major != old.major:
            return "major"
        elif new.minor != old.minor:
            return "minor"
        else:
            return "patch"
            
    def _get_version_info(self, block_id: str, version: str) -> Optional[Dict]:
        """Get version info by block and version"""
        for v in self.versions.get(block_id, []):
            if v["version"] == version:
                return v
        return None
        
    def _satisfies_constraint(self, version: str, constraint: str) -> bool:
        """Check if version satisfies constraint"""
        try:
            v = pkg_version.parse(version)
            
            # Handle simple constraints
            if constraint.startswith(">="):
                return v >= pkg_version.parse(constraint[2:])
            elif constraint.startswith(">"):
                return v > pkg_version.parse(constraint[1:])
            elif constraint.startswith("<="):
                return v <= pkg_version.parse(constraint[2:])
            elif constraint.startswith("<"):
                return v < pkg_version.parse(constraint[1:])
            elif constraint.startswith("^"):
                # Compatible version (^1.2.3 means >=1.2.3 <2.0.0)
                min_ver = pkg_version.parse(constraint[1:])
                return v >= min_ver and v.major == min_ver.major
            elif constraint.startswith("~"):
                # Approximately equivalent (~1.2.3 means >=1.2.3 <1.3.0)
                min_ver = pkg_version.parse(constraint[1:])
                return v >= min_ver and (v.major, v.minor) == (min_ver.major, min_ver.minor)
            else:
                # Exact version
                return v == pkg_version.parse(constraint)
        except:
            return False
            
    async def _build_dependency_tree(self, version_key: str, visited: set, depth: int = 0) -> Dict:
        """Recursively build dependency tree"""
        if version_key in visited or depth > 10:
            return {"error": "Circular dependency or too deep", "version": version_key}
            
        visited.add(version_key)
        
        deps = self.dependencies.get(version_key, {})
        tree = {
            "version": version_key,
            "dependencies": {}
        }
        
        for dep_name, constraint in deps.items():
            # Find matching version
            dep_versions = self.versions.get(dep_name, [])
            if dep_versions:
                latest = max(
                    dep_versions,
                    key=lambda x: pkg_version.parse(x["version"])
                )
                dep_key = f"{dep_name}@{latest['version']}"
                tree["dependencies"][dep_name] = {
                    "constraint": constraint,
                    "resolved": latest["version"],
                    "subtree": await self._build_dependency_tree(dep_key, visited.copy(), depth + 1)
                }
            else:
                tree["dependencies"][dep_name] = {
                    "constraint": constraint,
                    "resolved": None,
                    "error": "Block not found"
                }
                
        return tree
        
    def _count_deps(self, tree: Dict) -> int:
        """Count total dependencies in tree"""
        count = 0
        deps = tree.get("dependencies", {})
        for dep_info in deps.values():
            count += 1
            subtree = dep_info.get("subtree", {})
            if "dependencies" in subtree:
                count += self._count_deps(subtree)
        return count
        
    async def _cleanup_old_versions(self, block_id: str):
        """Remove old versions beyond max_kept"""
        versions = self.versions.get(block_id, [])
        if len(versions) <= self.config["max_versions_kept"]:
            return
            
        # Sort and keep newest
        sorted_versions = sorted(
            versions,
            key=lambda x: pkg_version.parse(x["version"]),
            reverse=True
        )
        
        self.versions[block_id] = sorted_versions[:self.config["max_versions_kept"]]
        
    def health(self) -> Dict:
        h = super().health()
        h["tracked_blocks"] = len(self.versions)
        h["total_versions"] = sum(len(v) for v in self.versions.values())
        h["deprecated_versions"] = len(self.deprecated)
        h["yanked_versions"] = sum(
            1 for versions in self.versions.values()
            for v in versions if v.get("yanked")
        )
        return h
