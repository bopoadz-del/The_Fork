"""
5-Tier Credibility Scorer Block

Tier 1 – Unverified  (0–19)  : raw claim, single source, no validation
Tier 2 – Plausible   (20–39) : schema + domain validation passed
Tier 3 – Corroborated(40–59) : cross-referenced with ≥2 independent sources
Tier 4 – Verified    (60–79) : benchmark-validated + reviewer sign-off
Tier 5 – Certified   (80–100): all 5 stages passed, traceable evidence chain
"""

import time
from typing import Any, Dict, List, Optional
from app.core.universal_base import UniversalBlock


TIER_DEFINITIONS = {
    1: {"label": "Unverified",   "range": (0,  19),  "color": "#e74c3c", "icon": "🔴"},
    2: {"label": "Plausible",    "range": (20, 39),  "color": "#e67e22", "icon": "🟠"},
    3: {"label": "Corroborated", "range": (40, 59),  "color": "#f1c40f", "icon": "🟡"},
    4: {"label": "Verified",     "range": (60, 79),  "color": "#2ecc71", "icon": "🟢"},
    5: {"label": "Certified",    "range": (80, 100), "color": "#3498db", "icon": "🔵"},
}

# Source quality weights (higher = more credible source)
SOURCE_WEIGHTS = {
    "measured_survey":      1.00,
    "as_built_drawing":     0.95,
    "stamped_drawing":      0.90,
    "engineer_estimate":    0.80,
    "quantity_surveyor":    0.80,
    "contractor_quote":     0.65,
    "subcontractor_quote":  0.60,
    "budgetary_estimate":   0.50,
    "rs_means":             0.75,
    "historical_benchmark": 0.70,
    "contractor_claim":     0.45,
    "verbal_instruction":   0.30,
    "assumption":           0.20,
    "unknown":              0.10,
}


class CredibilityScorerBlock(UniversalBlock):
    name = "credibility_scorer"
    version = "1.0.0"
    description = "5-tier credibility scoring: Unverified→Plausible→Corroborated→Verified→Certified"
    layer = 3
    tags = ["reasoning", "credibility", "validation", "scoring", "construction"]
    requires = []

    default_config = {
        "age_decay_days": 365,          # full decay after this many days
        "min_sources_for_corroboration": 2,
    }

    ui_schema = {
        "input": {
            "type": "json",
            "placeholder": '{"items": [{"id": "item_1", "value": 1250, "source": "engineer_estimate", "validation_stages_passed": [1,2,3], "cross_references": 2}]}',
            "multiline": True,
        },
        "output": {
            "type": "table",
            "fields": [
                {"name": "tier", "type": "number", "label": "Tier"},
                {"name": "tier_label", "type": "text", "label": "Tier Label"},
                {"name": "score", "type": "number", "label": "Score (0-100)"},
                {"name": "breakdown", "type": "json", "label": "Score Breakdown"},
            ],
        },
        "quick_actions": [
            {"icon": "🏅", "label": "Score Item",      "prompt": "Score credibility of this data item"},
            {"icon": "📊", "label": "Score Dataset",   "prompt": "Score all items and return tier distribution"},
            {"icon": "📋", "label": "Tier Reference",  "prompt": "Show tier definitions and scoring criteria"},
        ],
    }

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        data = input_data if isinstance(input_data, dict) else {}

        operation = data.get("operation") or params.get("operation", "score")

        if operation == "tier_reference":
            return self._tier_reference()

        items = data.get("items", [])
        if not items and "id" in data:
            items = [data]   # single item passed directly

        if not items:
            return {"status": "error", "error": "Provide 'items' list or single item with 'id' field"}

        scored = [self._score_item(item) for item in items]

        if len(scored) == 1:
            s = scored[0]
            return {
                "status": "success",
                "item_id": s["id"],
                "tier": s["tier"],
                "tier_label": s["tier_label"],
                "tier_icon": s["tier_icon"],
                "score": s["score"],
                "breakdown": s["breakdown"],
                "recommendations": s["recommendations"],
                "all_scored": scored,
            }

        # Dataset summary
        tier_dist: Dict[str, int] = {}
        for s in scored:
            lbl = s["tier_label"]
            tier_dist[lbl] = tier_dist.get(lbl, 0) + 1

        avg_score = round(sum(s["score"] for s in scored) / len(scored), 1)
        overall_tier, overall_label = self._score_to_tier(avg_score)

        return {
            "status": "success",
            "item_count": len(scored),
            "average_score": avg_score,
            "tier": overall_tier,
            "tier_label": overall_label,
            "tier_icon": TIER_DEFINITIONS[overall_tier]["icon"],
            "tier_distribution": tier_dist,
            "all_scored": scored,
            "low_credibility_items": [s for s in scored if s["tier"] <= 2],
        }

    def _score_item(self, item: Dict) -> Dict:
        """Score a single evidence/data item across all credibility dimensions."""
        scores: Dict[str, float] = {}

        # ── Dimension 1: Validation stages passed (40 pts max) ────────────────
        stages = item.get("validation_stages_passed", [])
        stages_score = min(len(stages) / 5, 1.0) * 40
        scores["validation_stages"] = round(stages_score, 1)

        # ── Dimension 2: Source quality (25 pts max) ──────────────────────────
        source = str(item.get("source", "unknown")).lower().replace(" ", "_")
        source_weight = SOURCE_WEIGHTS.get(source, SOURCE_WEIGHTS["unknown"])
        scores["source_quality"] = round(source_weight * 25, 1)

        # ── Dimension 3: Cross-references (20 pts max) ────────────────────────
        min_refs = int(self.config.get("min_sources_for_corroboration", 2))
        cross_refs = int(item.get("cross_references", item.get("sources_count", 0)))
        ref_score = min(cross_refs / max(min_refs, 1), 1.0) * 20
        scores["cross_references"] = round(ref_score, 1)

        # ── Dimension 4: Data freshness (10 pts max, decays with age) ─────────
        age_days = item.get("age_days")
        if age_days is None:
            # Try to compute from timestamp
            ts = item.get("timestamp") or item.get("created_at")
            if ts:
                try:
                    age_days = (time.time() - float(ts)) / 86400
                except Exception:
                    age_days = 0
        if age_days is not None:
            decay = int(self.config.get("age_decay_days", 365))
            freshness = max(0.0, 1.0 - float(age_days) / decay)
            scores["freshness"] = round(freshness * 10, 1)
        else:
            scores["freshness"] = 5.0   # neutral if unknown

        # ── Dimension 5: Expert sign-off bonus (5 pts max) ────────────────────
        expert_flags = item.get("expert_reviewed", False) or item.get("stamped", False)
        scores["expert_review"] = 5.0 if expert_flags else 0.0

        total = round(sum(scores.values()), 1)
        tier, label = self._score_to_tier(total)

        recommendations = self._build_recommendations(item, scores, tier)

        return {
            "id": item.get("id", item.get("description", "unknown")),
            "tier": tier,
            "tier_label": label,
            "tier_icon": TIER_DEFINITIONS[tier]["icon"],
            "score": total,
            "breakdown": scores,
            "recommendations": recommendations,
        }

    def _score_to_tier(self, score: float) -> tuple:
        for tier_num, defn in sorted(TIER_DEFINITIONS.items(), reverse=True):
            lo, hi = defn["range"]
            if lo <= score <= hi:
                return tier_num, defn["label"]
        return 1, "Unverified"

    def _build_recommendations(self, item: Dict, scores: Dict, tier: int) -> List[str]:
        recs = []
        if scores.get("validation_stages", 0) < 20:
            recs.append("Run 5-stage validation to increase score by up to 40 pts")
        if scores.get("source_quality", 0) < 15:
            source = item.get("source", "unknown")
            recs.append(f"Source '{source}' has low weight — obtain engineer estimate or measured survey")
        if scores.get("cross_references", 0) < 10:
            recs.append("Add ≥2 independent cross-references to reach Corroborated tier")
        if scores.get("freshness", 0) < 5:
            recs.append("Data is stale — refresh with current market rates")
        if scores.get("expert_review", 0) == 0 and tier >= 3:
            recs.append("Add expert review / stamp to unlock Certified tier")
        return recs

    def _tier_reference(self) -> Dict:
        return {
            "status": "success",
            "tier_definitions": TIER_DEFINITIONS,
            "source_weights": SOURCE_WEIGHTS,
            "scoring_dimensions": {
                "validation_stages":  "0–40 pts  — stages 1-5 passed (8 pts each)",
                "source_quality":     "0–25 pts  — measured survey=25, assumption=5",
                "cross_references":   "0–20 pts  — scaled to min_sources_for_corroboration",
                "freshness":          "0–10 pts  — decays linearly over age_decay_days",
                "expert_review":      "0–5 pts   — bonus for stamped/reviewed items",
            },
            "tier": 1, "tier_label": "Reference", "score": 0, "breakdown": {},
        }
