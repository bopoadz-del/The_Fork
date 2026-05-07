"""Team Block - Multi-tenant team/organization management

Handles team workspaces, shared resources, role-based access control,
member invitations, and team-scoped billing.
"""

from blocks.base import LegoBlock
from typing import Dict, Any, List, Optional
import asyncio
import hashlib
import secrets
from datetime import datetime, timedelta
from enum import Enum


class TeamRole(Enum):
    OWNER = "owner"           # Full control, can delete team
    ADMIN = "admin"           # Manage members, billing, settings
    DEVELOPER = "developer"   # Deploy blocks, access APIs
    MEMBER = "member"         # Use blocks, view resources
    VIEWER = "viewer"         # Read-only access


class TeamBlock(LegoBlock):
    """
    Multi-tenant team/organization management.
    Beyond individual API keys - team workspaces, shared resources, billing.
    """
    name = "team"
    version = "1.0.0"
    requires = ["auth", "database", "billing"]
    layer = 3
    tags = ["platform", "multi-tenant", "enterprise", "collaboration"]
    
    default_config = {
        "max_members_per_team": 50,
        "max_teams_per_org": 10,
        "default_role": "member",
        "invitation_expiry_hours": 48,
        "enable_sso": False,
        "require_2fa_for_admins": True
    }
    
    ROLE_HIERARCHY = {
        TeamRole.OWNER: ["admin", "developer", "member", "viewer"],
        TeamRole.ADMIN: ["developer", "member", "viewer"],
        TeamRole.DEVELOPER: ["member", "viewer"],
        TeamRole.MEMBER: ["viewer"],
        TeamRole.VIEWER: []
    }
    
    ROLE_PERMISSIONS = {
        TeamRole.OWNER: ["*"],  # All permissions
        TeamRole.ADMIN: [
            "team.manage", "billing.manage", "members.invite",
            "members.remove", "settings.edit", "audit.read"
        ],
        TeamRole.DEVELOPER: [
            "blocks.deploy", "blocks.edit", "api.access",
            "logs.read", "metrics.read"
        ],
        TeamRole.MEMBER: [
            "blocks.use", "dashboard.view", "reports.read"
        ],
        TeamRole.VIEWER: [
            "dashboard.view", "reports.read"
        ]
    }
    
    def __init__(self, hal_block=None, config: Dict = None):
        super().__init__(hal_block, config)
        self.teams: Dict[str, Dict] = {}  # In-memory cache
        self.memberships: Dict[str, List[Dict]] = {}  # team_id -> members
        self.invitations: Dict[str, Dict] = {}  # token -> invitation
        
    async def initialize(self) -> bool:
        """Initialize team management"""
        print("👥 Team Block initializing...")
        print(f"   Max members/team: {self.config['max_members_per_team']}")
        print(f"   Max teams/org: {self.config['max_teams_per_org']}")
        
        # TODO: Create database tables if using persistent storage
        # - teams: id, name, slug, owner_id, plan, created_at
        # - team_memberships: team_id, user_id, role, joined_at
        # - invitations: token, team_id, email, role, expires_at
        # - team_resources: team_id, resource_type, resource_id, quota
        
        self.initialized = True
        return True
        
    async def execute(self, input_data: Dict) -> Dict:
        """Execute team management actions"""
        action = input_data.get("action")
        
        actions = {
            "create_team": self._create_team,
            "delete_team": self._delete_team,
            "invite_member": self._invite_member,
            "accept_invitation": self._accept_invitation,
            "remove_member": self._remove_member,
            "set_role": self._set_role,
            "get_team": self._get_team,
            "list_teams": self._list_teams,
            "get_members": self._get_members,
            "get_team_context": self._get_context,
            "switch_team": self._switch_team,
            "check_permission": self._check_permission,
            "update_team": self._update_team,
            "leave_team": self._leave_team
        }
        
        if action in actions:
            return await actions[action](input_data)
            
        return {"error": f"Unknown action: {action}", "available": list(actions.keys())}
        
    async def _create_team(self, data: Dict) -> Dict:
        """Create a new team"""
        user_id = data.get("user_id")
        team_name = data.get("name")
        team_slug = data.get("slug", self._slugify(team_name))
        plan = data.get("plan", "free")
        
        if not user_id or not team_name:
            return {"error": "user_id and name required"}
            
        # Check user's team limit
        user_teams = await self._get_user_teams(user_id)
        if len(user_teams) >= self.config["max_teams_per_org"]:
            return {"error": f"Maximum {self.config['max_teams_per_org']} teams per organization"}
            
        # Check slug uniqueness
        if any(t.get("slug") == team_slug for t in self.teams.values()):
            return {"error": f"Team slug '{team_slug}' already exists"}
            
        team_id = f"team_{secrets.token_hex(8)}"
        team = {
            "id": team_id,
            "name": team_name,
            "slug": team_slug,
            "owner_id": user_id,
            "plan": plan,
            "created_at": datetime.utcnow().isoformat(),
            "settings": {
                "allow_guest_invites": True,
                "require_approval": False,
                "public_dashboard": False
            },
            "quotas": {
                "members_used": 1,
                "members_limit": self.config["max_members_per_team"],
                "blocks_used": 0,
                "blocks_limit": 100 if plan == "free" else 1000
            }
        }
        
        self.teams[team_id] = team
        
        # Add creator as owner
        membership = {
            "team_id": team_id,
            "user_id": user_id,
            "role": TeamRole.OWNER.value,
            "joined_at": datetime.utcnow().isoformat(),
            "invited_by": None
        }
        self.memberships[team_id] = [membership]
        
        print(f"   ✓ Created team: {team_name} ({team_slug})")
        
        return {
            "team_id": team_id,
            "name": team_name,
            "slug": team_slug,
            "created": True,
            "owner": user_id
        }
        
    async def _delete_team(self, data: Dict) -> Dict:
        """Delete a team (owner only)"""
        team_id = data.get("team_id")
        user_id = data.get("user_id")
        
        team = self.teams.get(team_id)
        if not team:
            return {"error": "Team not found"}
            
        # Check ownership
        if team["owner_id"] != user_id:
            return {"error": "Only team owner can delete team"}
            
        # Clean up
        del self.teams[team_id]
        if team_id in self.memberships:
            del self.memberships[team_id]
            
        print(f"   ✓ Deleted team: {team_id}")
        
        return {"deleted": True, "team_id": team_id}
        
    async def _invite_member(self, data: Dict) -> Dict:
        """Invite a user to join a team"""
        team_id = data.get("team_id")
        invited_by = data.get("user_id")
        email = data.get("email")
        role = data.get("role", self.config["default_role"])
        
        team = self.teams.get(team_id)
        if not team:
            return {"error": "Team not found"}
            
        # Check inviter has permission
        if not await self._has_permission(team_id, invited_by, "members.invite"):
            return {"error": "Permission denied: cannot invite members"}
            
        # Check team size limit
        members = self.memberships.get(team_id, [])
        if len(members) >= self.config["max_members_per_team"]:
            return {"error": "Team member limit reached"}
            
        # Generate invitation token
        token = secrets.token_urlsafe(32)
        invitation = {
            "token": token,
            "team_id": team_id,
            "email": email,
            "role": role,
            "invited_by": invited_by,
            "created_at": datetime.utcnow().isoformat(),
            "expires_at": (datetime.utcnow() + timedelta(
                hours=self.config["invitation_expiry_hours"]
            )).isoformat()
        }
        
        self.invitations[token] = invitation
        
        # TODO: Send email with invitation link
        # TODO: Add to database
        
        print(f"   ✓ Invited {email} to {team['name']} as {role}")
        
        return {
            "invited": True,
            "email": email,
            "role": role,
            "expires_in_hours": self.config["invitation_expiry_hours"]
        }
        
    async def _accept_invitation(self, data: Dict) -> Dict:
        """Accept a team invitation"""
        token = data.get("token")
        user_id = data.get("user_id")
        
        invitation = self.invitations.get(token)
        if not invitation:
            return {"error": "Invalid or expired invitation"}
            
        # Check expiry
        expires = datetime.fromisoformat(invitation["expires_at"])
        if datetime.utcnow() > expires:
            return {"error": "Invitation expired"}
            
        team_id = invitation["team_id"]
        
        # Add membership
        membership = {
            "team_id": team_id,
            "user_id": user_id,
            "role": invitation["role"],
            "joined_at": datetime.utcnow().isoformat(),
            "invited_by": invitation["invited_by"]
        }
        
        if team_id not in self.memberships:
            self.memberships[team_id] = []
        self.memberships[team_id].append(membership)
        
        # Update quota
        if team_id in self.teams:
            self.teams[team_id]["quotas"]["members_used"] = len(self.memberships[team_id])
            
        # Clean up invitation
        del self.invitations[token]
        
        print(f"   ✓ {user_id} joined team {team_id}")
        
        return {
            "joined": True,
            "team_id": team_id,
            "role": invitation["role"]
        }
        
    async def _remove_member(self, data: Dict) -> Dict:
        """Remove a member from team"""
        team_id = data.get("team_id")
        user_id = data.get("user_id")  # Remover
        target_user_id = data.get("target_user_id")  # Person being removed
        
        team = self.teams.get(team_id)
        if not team:
            return {"error": "Team not found"}
            
        # Check permissions
        remover_membership = self._get_membership(team_id, user_id)
        target_membership = self._get_membership(team_id, target_user_id)
        
        if not remover_membership:
            return {"error": "Not a team member"}
            
        if not target_membership:
            return {"error": "Target user is not a team member"}
            
        # Cannot remove owner unless you're owner
        if target_membership["role"] == TeamRole.OWNER.value and team["owner_id"] != user_id:
            return {"error": "Cannot remove team owner"}
            
        # Check remover can remove target (role hierarchy)
        remover_role = TeamRole(remover_membership["role"])
        target_role = TeamRole(target_membership["role"])
        
        if target_role.value not in self.ROLE_HIERARCHY.get(remover_role, []):
            return {"error": f"Cannot remove user with role {target_role.value}"}
            
        # Remove
        self.memberships[team_id] = [
            m for m in self.memberships[team_id] 
            if m["user_id"] != target_user_id
        ]
        
        # Update quota
        self.teams[team_id]["quotas"]["members_used"] = len(self.memberships[team_id])
        
        print(f"   ✓ Removed {target_user_id} from team {team_id}")
        
        return {"removed": True, "user_id": target_user_id}
        
    async def _set_role(self, data: Dict) -> Dict:
        """Change a member's role"""
        team_id = data.get("team_id")
        user_id = data.get("user_id")  # Changer
        target_user_id = data.get("target_user_id")
        new_role = data.get("role")
        
        # Validate new role
        if new_role not in [r.value for r in TeamRole]:
            return {"error": f"Invalid role: {new_role}"}
            
        team = self.teams.get(team_id)
        if not team:
            return {"error": "Team not found"}
            
        # Only admin+ can change roles
        if not await self._has_permission(team_id, user_id, "team.manage"):
            return {"error": "Permission denied"}
            
        # Cannot change owner's role unless you're owner
        if target_user_id == team["owner_id"] and user_id != team["owner_id"]:
            return {"error": "Cannot change owner's role"}
            
        # Update membership
        for m in self.memberships.get(team_id, []):
            if m["user_id"] == target_user_id:
                m["role"] = new_role
                print(f"   ✓ Changed {target_user_id} role to {new_role}")
                return {"updated": True, "user_id": target_user_id, "new_role": new_role}
                
        return {"error": "Target user not found in team"}
        
    async def _get_team(self, data: Dict) -> Dict:
        """Get team details"""
        team_id = data.get("team_id")
        team = self.teams.get(team_id)
        
        if not team:
            return {"error": "Team not found"}
            
        # Add member count
        team_data = team.copy()
        team_data["member_count"] = len(self.memberships.get(team_id, []))
        
        return {"team": team_data}
        
    async def _list_teams(self, data: Dict) -> Dict:
        """List teams for a user"""
        user_id = data.get("user_id")
        teams = await self._get_user_teams(user_id)
        
        return {
            "teams": teams,
            "count": len(teams)
        }
        
    async def _get_members(self, data: Dict) -> Dict:
        """Get team members"""
        team_id = data.get("team_id")
        
        members = self.memberships.get(team_id, [])
        team = self.teams.get(team_id, {})
        
        # Enrich with team name
        for m in members:
            m["team_name"] = team.get("name", "Unknown")
            
        return {
            "members": members,
            "count": len(members),
            "limit": self.config["max_members_per_team"]
        }
        
    async def _get_context(self, data: Dict) -> Dict:
        """Inject team context into request"""
        user_id = data.get("user_id")
        team_id = data.get("team_id")
        
        team = self.teams.get(team_id)
        membership = self._get_membership(team_id, user_id)
        
        if not team or not membership:
            return {"error": "Team access denied"}
            
        return {
            "team_id": team_id,
            "team_name": team["name"],
            "team_slug": team["slug"],
            "role": membership["role"],
            "permissions": self.ROLE_PERMISSIONS.get(
                TeamRole(membership["role"]), []
            ),
            "plan": team["plan"],
            "quotas": team["quotas"]
        }
        
    async def _switch_team(self, data: Dict) -> Dict:
        """Switch user's active team context"""
        user_id = data.get("user_id")
        team_id = data.get("team_id")
        
        # Verify membership
        membership = self._get_membership(team_id, user_id)
        if not membership:
            return {"error": "Not a member of this team"}
            
        team = self.teams.get(team_id)
        
        # TODO: Update user's active team in session/auth
        
        return {
            "switched": True,
            "team_id": team_id,
            "team_name": team["name"],
            "role": membership["role"]
        }
        
    async def _check_permission(self, data: Dict) -> Dict:
        """Check if user has specific permission in team"""
        team_id = data.get("team_id")
        user_id = data.get("user_id")
        permission = data.get("permission")
        
        has = await self._has_permission(team_id, user_id, permission)
        
        return {
            "has_permission": has,
            "permission": permission
        }
        
    async def _update_team(self, data: Dict) -> Dict:
        """Update team settings"""
        team_id = data.get("team_id")
        user_id = data.get("user_id")
        
        if not await self._has_permission(team_id, user_id, "settings.edit"):
            return {"error": "Permission denied"}
            
        team = self.teams.get(team_id)
        if not team:
            return {"error": "Team not found"}
            
        # Update allowed fields
        allowed = ["name", "settings"]
        for field in allowed:
            if field in data:
                team[field] = data[field]
                
        return {"updated": True, "team_id": team_id}
        
    async def _leave_team(self, data: Dict) -> Dict:
        """Leave a team (cannot leave if owner)"""
        team_id = data.get("team_id")
        user_id = data.get("user_id")
        
        team = self.teams.get(team_id)
        if not team:
            return {"error": "Team not found"}
            
        # Owner cannot leave, must transfer ownership first
        if team["owner_id"] == user_id:
            return {"error": "Owner cannot leave team. Transfer ownership first."}
            
        # Remove membership
        self.memberships[team_id] = [
            m for m in self.memberships.get(team_id, [])
            if m["user_id"] != user_id
        ]
        
        # Update quota
        self.teams[team_id]["quotas"]["members_used"] = len(self.memberships[team_id])
        
        return {"left": True, "team_id": team_id}
        
    # Helper methods
    async def _has_permission(self, team_id: str, user_id: str, permission: str) -> bool:
        """Check if user has permission"""
        membership = self._get_membership(team_id, user_id)
        if not membership:
            return False
            
        role = TeamRole(membership["role"])
        permissions = self.ROLE_PERMISSIONS.get(role, [])
        
        return "*" in permissions or permission in permissions
        
    def _get_membership(self, team_id: str, user_id: str) -> Optional[Dict]:
        """Get user's membership in team"""
        for m in self.memberships.get(team_id, []):
            if m["user_id"] == user_id:
                return m
        return None
        
    async def _get_user_teams(self, user_id: str) -> List[Dict]:
        """Get all teams for a user"""
        teams = []
        for team_id, members in self.memberships.items():
            for m in members:
                if m["user_id"] == user_id:
                    team = self.teams.get(team_id, {}).copy()
                    team["role"] = m["role"]
                    team["joined_at"] = m["joined_at"]
                    teams.append(team)
        return teams
        
    def _slugify(self, name: str) -> str:
        """Convert name to URL-safe slug"""
        import re
        slug = re.sub(r'[^\w\s-]', '', name.lower())
        slug = re.sub(r'[-\s]+', '-', slug)
        return slug[:50]
        
    def health(self) -> Dict:
        h = super().health()
        h["teams_count"] = len(self.teams)
        h["total_memberships"] = sum(len(m) for m in self.memberships.values())
        h["pending_invitations"] = len(self.invitations)
        return h
