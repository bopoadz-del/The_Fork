"""
Evidence Vault Block
Append-only immutable evidence store with:
- SHA-256 content hash for tamper detection
- Credibility tier tagging
- Validation stage linkage
- Full-text search + filter by type/tier/project/date
- Evidence chain (link items together)
- Audit trail export
"""

import json
import os
import time
import hashlib
import uuid
from typing import Any, Dict, List, Optional
from app.core.universal_base import UniversalBlock

_VAULT_PATH = os.environ.get("EVIDENCE_VAULT_PATH", "/tmp/cerebrum_evidence_vault.json")


class EvidenceVaultBlock(UniversalBlock):
    name = "evidence_vault"
    version = "1.0.0"
    description = "Immutable evidence store: append-only vault with SHA-256 integrity, credibility tagging, audit trail"
    layer = 3
    tags = ["reasoning", "evidence", "vault", "audit", "immutable", "construction"]
    requires = []

    default_config = {
        "vault_path": _VAULT_PATH,
        "max_search_results": 100,
    }

    # Evidence types
    EVIDENCE_TYPES = [
        "calculation", "measurement", "benchmark", "test_result",
        "drawing", "specification", "contract", "correspondence",
        "site_photo", "inspection_report", "claim", "decision",
        "assumption", "expert_opinion", "validation_result",
    ]

    ui_schema = {
        "input": {
            "type": "json",
            "placeholder": '{"operation": "store", "evidence": {"type": "calculation", "content": {...}, "source": "engineer_estimate", "project_id": "PRJ-001"}}',
            "multiline": True,
        },
        "output": {
            "type": "json",
            "fields": [
                {"name": "evidence_id",   "type": "text",   "label": "Evidence ID"},
                {"name": "hash",          "type": "text",   "label": "SHA-256 Hash"},
                {"name": "credibility_tier", "type": "number", "label": "Tier"},
                {"name": "audit_trail",   "type": "list",   "label": "Audit Trail"},
            ],
        },
        "quick_actions": [
            {"icon": "🔒", "label": "Store Evidence",  "prompt": "Store this calculation as evidence"},
            {"icon": "🔍", "label": "Search Vault",    "prompt": "Search evidence vault by project"},
            {"icon": "🔗", "label": "Build Chain",     "prompt": "Link related evidence items"},
            {"icon": "📋", "label": "Audit Trail",     "prompt": "Export full audit trail for this project"},
        ],
    }

    def __init__(self, hal_block=None, config: Dict = None):
        super().__init__(hal_block, config)
        self._vault: Dict = self._load_vault()

    def _load_vault(self) -> Dict:
        path = self.config.get("vault_path", _VAULT_PATH) if hasattr(self, "config") else _VAULT_PATH
        try:
            if os.path.exists(path):
                with open(path) as f:
                    return json.load(f)
        except Exception:
            pass
        return {"entries": {}, "chains": {}, "index": {"by_project": {}, "by_type": {}, "by_tier": {}}}

    def _save_vault(self):
        path = self.config.get("vault_path", _VAULT_PATH)
        try:
            with open(path, "w") as f:
                json.dump(self._vault, f, indent=2)
        except Exception:
            pass

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        data = input_data if isinstance(input_data, dict) else {}

        operation = data.get("operation") or params.get("operation", "store")

        ops = {
            "store":         self._store,
            "retrieve":      self._retrieve,
            "search":        self._search,
            "verify":        self._verify,
            "chain":         self._chain,
            "get_chain":     self._get_chain,
            "audit_trail":   self._audit_trail,
            "stats":         self._stats,
            "list_types":    self._list_types,
        }

        handler = ops.get(operation)
        if not handler:
            return {"status": "error", "error": f"Unknown operation: '{operation}'. Use: {list(ops.keys())}"}
        return await handler(data, params)

    # ── Store ──────────────────────────────────────────────────────────────────

    async def _store(self, data: Dict, params: Dict) -> Dict:
        evidence = data.get("evidence", {})
        if not evidence:
            evidence = {k: v for k, v in data.items() if k not in ("operation",)}

        etype = evidence.get("type", "calculation")
        content = evidence.get("content", evidence)
        source = evidence.get("source", "unknown")
        project_id = evidence.get("project_id") or data.get("project_id", "default")
        tags = evidence.get("tags", [])
        credibility_tier = int(evidence.get("credibility_tier", 1))
        validation_stages = evidence.get("validation_stages_passed", [])
        parent_ids = evidence.get("parent_ids", [])     # evidence chain links

        # Generate ID and hash
        ev_id = "EV-" + str(uuid.uuid4())[:8].upper()
        content_str = json.dumps(content, sort_keys=True, default=str)
        content_hash = hashlib.sha256(content_str.encode()).hexdigest()
        ts = time.time()

        entry = {
            "id": ev_id,
            "type": etype,
            "source": source,
            "project_id": project_id,
            "content": content,
            "hash": content_hash,
            "credibility_tier": credibility_tier,
            "validation_stages_passed": validation_stages,
            "tags": tags,
            "parent_ids": parent_ids,
            "created_at": ts,
            "created_at_iso": _ts_iso(ts),
            "immutable": True,
        }

        self._vault["entries"][ev_id] = entry

        # Update indices
        idx = self._vault["index"]
        idx.setdefault("by_project", {}).setdefault(project_id, []).append(ev_id)
        idx.setdefault("by_type", {}).setdefault(etype, []).append(ev_id)
        idx.setdefault("by_tier", {}).setdefault(str(credibility_tier), []).append(ev_id)

        # Build chain link
        if parent_ids:
            for pid in parent_ids:
                chain_key = f"{pid}→{ev_id}"
                self._vault["chains"][chain_key] = {"parent": pid, "child": ev_id, "ts": ts}

        self._save_vault()

        return {
            "status": "success",
            "evidence_id": ev_id,
            "hash": content_hash,
            "credibility_tier": credibility_tier,
            "project_id": project_id,
            "created_at": entry["created_at_iso"],
            "vault_size": len(self._vault["entries"]),
        }

    # ── Retrieve ───────────────────────────────────────────────────────────────

    async def _retrieve(self, data: Dict, params: Dict) -> Dict:
        ev_id = data.get("evidence_id") or params.get("evidence_id")
        if not ev_id:
            return {"status": "error", "error": "evidence_id required"}

        entry = self._vault["entries"].get(ev_id)
        if not entry:
            return {"status": "error", "error": f"Evidence '{ev_id}' not found"}

        # Verify hash on retrieval
        content_str = json.dumps(entry["content"], sort_keys=True, default=str)
        current_hash = hashlib.sha256(content_str.encode()).hexdigest()
        tampered = current_hash != entry["hash"]

        return {
            "status": "success",
            "evidence": entry,
            "tampered": tampered,
            "integrity": "COMPROMISED" if tampered else "INTACT",
            "credibility_tier": entry.get("credibility_tier", 1),
            "audit_trail": self._build_audit_trail(ev_id),
        }

    # ── Search ─────────────────────────────────────────────────────────────────

    async def _search(self, data: Dict, params: Dict) -> Dict:
        project_id = data.get("project_id") or params.get("project_id")
        etype      = data.get("type")
        tier       = data.get("credibility_tier")
        tags       = data.get("tags", [])
        query      = data.get("query", "").lower()
        max_results = int(data.get("max_results", self.config.get("max_search_results", 100)))

        entries = list(self._vault["entries"].values())

        # Filter
        if project_id:
            entries = [e for e in entries if e.get("project_id") == project_id]
        if etype:
            entries = [e for e in entries if e.get("type") == etype]
        if tier is not None:
            entries = [e for e in entries if e.get("credibility_tier") == int(tier)]
        if tags:
            entries = [e for e in entries if any(t in e.get("tags", []) for t in tags)]
        if query:
            entries = [
                e for e in entries
                if query in json.dumps(e.get("content", {}), default=str).lower()
                or query in str(e.get("source", "")).lower()
                or query in str(e.get("type", "")).lower()
            ]

        # Sort by created_at desc
        entries.sort(key=lambda e: e.get("created_at", 0), reverse=True)
        entries = entries[:max_results]

        return {
            "status": "success",
            "count": len(entries),
            "results": entries,
            "filters_applied": {
                "project_id": project_id, "type": etype,
                "credibility_tier": tier, "tags": tags, "query": query,
            },
        }

    # ── Verify integrity ───────────────────────────────────────────────────────

    async def _verify(self, data: Dict, params: Dict) -> Dict:
        ev_ids = data.get("evidence_ids", [])
        project_id = data.get("project_id")

        if project_id:
            ev_ids = self._vault["index"].get("by_project", {}).get(project_id, [])
        if not ev_ids:
            ev_ids = list(self._vault["entries"].keys())

        results = []
        tampered_count = 0
        for ev_id in ev_ids:
            entry = self._vault["entries"].get(ev_id)
            if not entry:
                continue
            content_str = json.dumps(entry["content"], sort_keys=True, default=str)
            current_hash = hashlib.sha256(content_str.encode()).hexdigest()
            tampered = current_hash != entry["hash"]
            if tampered:
                tampered_count += 1
            results.append({
                "id": ev_id,
                "integrity": "COMPROMISED" if tampered else "INTACT",
                "stored_hash": entry["hash"],
                "computed_hash": current_hash,
            })

        return {
            "status": "success",
            "verified_count": len(results),
            "tampered_count": tampered_count,
            "all_intact": tampered_count == 0,
            "results": results,
        }

    # ── Chain management ───────────────────────────────────────────────────────

    async def _chain(self, data: Dict, params: Dict) -> Dict:
        parent_id = data.get("parent_id")
        child_id  = data.get("child_id")
        relation  = data.get("relation", "derived_from")

        if not parent_id or not child_id:
            return {"status": "error", "error": "parent_id and child_id required"}

        chain_key = f"{parent_id}→{child_id}"
        self._vault["chains"][chain_key] = {
            "parent": parent_id, "child": child_id,
            "relation": relation, "ts": time.time(),
        }
        self._save_vault()

        return {
            "status": "success",
            "chain_key": chain_key,
            "parent_id": parent_id,
            "child_id": child_id,
            "relation": relation,
        }

    async def _get_chain(self, data: Dict, params: Dict) -> Dict:
        ev_id = data.get("evidence_id") or params.get("evidence_id")
        if not ev_id:
            return {"status": "error", "error": "evidence_id required"}

        parents  = [v for v in self._vault["chains"].values() if v["child"]  == ev_id]
        children = [v for v in self._vault["chains"].values() if v["parent"] == ev_id]

        return {
            "status": "success",
            "evidence_id": ev_id,
            "parent_links": parents,
            "child_links": children,
            "chain_depth": self._chain_depth(ev_id),
        }

    # ── Audit trail ────────────────────────────────────────────────────────────

    async def _audit_trail(self, data: Dict, params: Dict) -> Dict:
        project_id = data.get("project_id") or params.get("project_id")
        ev_id      = data.get("evidence_id")

        if ev_id:
            trail = self._build_audit_trail(ev_id)
            return {"status": "success", "audit_trail": trail, "evidence_id": ev_id}

        if not project_id:
            return {"status": "error", "error": "project_id or evidence_id required"}

        ev_ids = self._vault["index"].get("by_project", {}).get(project_id, [])
        trail = []
        for eid in ev_ids:
            entry = self._vault["entries"].get(eid, {})
            trail.append({
                "id": eid,
                "type": entry.get("type"),
                "source": entry.get("source"),
                "created_at": entry.get("created_at_iso"),
                "credibility_tier": entry.get("credibility_tier"),
                "hash": entry.get("hash","")[:16] + "...",
            })
        trail.sort(key=lambda x: x.get("created_at",""))

        return {
            "status": "success",
            "project_id": project_id,
            "audit_trail": trail,
            "entry_count": len(trail),
        }

    # ── Stats ──────────────────────────────────────────────────────────────────

    async def _stats(self, data: Dict, params: Dict) -> Dict:
        entries = self._vault["entries"]
        tier_dist: Dict[str, int] = {}
        type_dist: Dict[str, int] = {}
        for e in entries.values():
            t = str(e.get("credibility_tier", 1))
            tier_dist[t] = tier_dist.get(t, 0) + 1
            et = e.get("type", "unknown")
            type_dist[et] = type_dist.get(et, 0) + 1

        return {
            "status": "success",
            "total_entries": len(entries),
            "total_chains": len(self._vault["chains"]),
            "projects": list(self._vault["index"].get("by_project", {}).keys()),
            "tier_distribution": tier_dist,
            "type_distribution": type_dist,
        }

    async def _list_types(self, data: Dict, params: Dict) -> Dict:
        return {"status": "success", "evidence_types": self.EVIDENCE_TYPES}

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _build_audit_trail(self, ev_id: str) -> List[Dict]:
        trail = []
        visited = set()

        def _walk(eid):
            if eid in visited:
                return
            visited.add(eid)
            entry = self._vault["entries"].get(eid)
            if entry:
                trail.append({
                    "id": eid,
                    "type": entry.get("type"),
                    "source": entry.get("source"),
                    "created_at": entry.get("created_at_iso"),
                    "credibility_tier": entry.get("credibility_tier"),
                    "hash_prefix": entry.get("hash","")[:16] + "...",
                })
            for v in self._vault["chains"].values():
                if v["child"] == eid:
                    _walk(v["parent"])

        _walk(ev_id)
        return trail

    def _chain_depth(self, ev_id: str, visited: Optional[set] = None) -> int:
        if visited is None:
            visited = set()
        if ev_id in visited:
            return 0
        visited.add(ev_id)
        parents = [v["parent"] for v in self._vault["chains"].values() if v["child"] == ev_id]
        if not parents:
            return 0
        return 1 + max(self._chain_depth(p, visited) for p in parents)


def _ts_iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
