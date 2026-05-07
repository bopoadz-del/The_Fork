"""SymPy Reasoning Block - Symbolic variance analysis + construction recommendations"""

import os
from typing import Any, Dict, List
from app.core.universal_base import UniversalBlock


class SymPyReasoningBlock(UniversalBlock):
    name = "sympy_reasoning"
    version = "1.0.0"
    description = "Heavy reasoning engine: symbolic variance analysis + construction recommendations"
    layer = 3
    tags = ["domain", "construction", "reasoning", "math", "ai"]
    requires = []

    default_config = {
        "variance_threshold_pct": 10.0,
        "confidence_floor": 0.7,
    }

    ui_schema = {
        "input": {
            "type": "json",
            "placeholder": '{"boq_data": [...], "drawing_data": {}, "spec_data": {}, "historical_benchmarks": {}}',
            "multiline": True,
        },
        "output": {
            "type": "table",
            "fields": [
                {"name": "recommendations", "type": "list", "label": "Recommendations"},
                {"name": "variances", "type": "list", "label": "Variances"},
                {"name": "cost_impacts", "type": "list", "label": "Cost Impacts"},
            ],
        },
        "quick_actions": [
            {"icon": "📊", "label": "Analyze BOQ", "prompt": "Analyze BOQ vs historical benchmarks"},
            {"icon": "⚠️", "label": "Find Variances", "prompt": "Find cost variances above 10%"},
        ],
    }

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        data = input_data if isinstance(input_data, dict) else {}

        boq_data = data.get("boq_data", [])
        drawing_data = data.get("drawing_data", {})
        spec_data = data.get("spec_data", {})
        historical_benchmarks = data.get("historical_benchmarks", {})

        try:
            import sympy as sp
        except ImportError:
            return {"status": "error", "error": "sympy not installed. Run: pip install sympy"}

        threshold = float(
            params.get("variance_threshold_pct",
                        self.config.get("variance_threshold_pct", 10.0))
        )

        variances = self._compute_variances(boq_data, historical_benchmarks, sp, threshold)
        cost_impacts = self._compute_cost_impacts(variances, sp)
        recommendations = self._generate_recommendations(variances, spec_data, drawing_data)

        return {
            "status": "success",
            "recommendations": recommendations,
            "variances": variances,
            "cost_impacts": cost_impacts,
            "items_analyzed": len(boq_data),
            "high_variance_count": sum(1 for v in variances if v.get("severity") == "high"),
        }

    def _compute_variances(
        self, boq_data: List, benchmarks: Dict, sp, threshold: float
    ) -> List[Dict]:
        variances = []
        for item in boq_data:
            key = (
                item.get("item_key")
                or item.get("description", "").lower().replace(" ", "_")
            )
            actual = float(item.get("unit_cost") or item.get("rate", 0))
            benchmark = benchmarks.get(key, {})
            avg_cost = float(benchmark.get("avg_cost", 0))
            std_dev = float(benchmark.get("std_dev", 0))

            if avg_cost <= 0:
                continue

            actual_sym = sp.Float(actual)
            avg_sym = sp.Float(avg_cost)
            variance_pct = float(((actual_sym - avg_sym) / avg_sym) * 100)
            z_score = (
                float((actual_sym - avg_sym) / sp.Float(std_dev))
                if std_dev > 0
                else 0.0
            )

            if abs(variance_pct) > threshold * 2:
                severity = "high"
            elif abs(variance_pct) > threshold:
                severity = "medium"
            else:
                severity = "low"

            variances.append(
                {
                    "item_key": key,
                    "description": item.get("description", key),
                    "actual_cost": actual,
                    "benchmark_avg": avg_cost,
                    "variance_pct": round(variance_pct, 2),
                    "z_score": round(z_score, 3),
                    "severity": severity,
                    "quantity": item.get("quantity", 1),
                    "unit": item.get("unit", ""),
                }
            )

        return sorted(variances, key=lambda x: abs(x["variance_pct"]), reverse=True)

    def _compute_cost_impacts(self, variances: List[Dict], sp) -> List[Dict]:
        impacts = []
        for v in variances:
            qty = sp.Float(v.get("quantity", 1))
            actual = sp.Float(v.get("actual_cost", 0))
            benchmark = sp.Float(v.get("benchmark_avg", 0))
            impact = float((actual - benchmark) * qty)
            if abs(impact) < 0.01:
                continue
            impacts.append(
                {
                    "item_key": v["item_key"],
                    "cost_impact_usd": round(impact, 2),
                    "direction": "over" if impact > 0 else "under",
                    "severity": v["severity"],
                }
            )
        return sorted(impacts, key=lambda x: abs(x["cost_impact_usd"]), reverse=True)

    def _generate_recommendations(
        self, variances: List[Dict], spec_data: Dict, drawing_data: Dict
    ) -> List[Dict]:
        templates = {
            "high_over": {
                "text": "URGENT: {item} is {pct:.1f}% over benchmark. Review supplier pricing and re-tender.",
                "severity": "critical",
                "action_items": [
                    "Re-tender to 3 suppliers",
                    "Check market rates",
                    "Negotiate volume discount",
                ],
            },
            "high_under": {
                "text": "ALERT: {item} is {pct:.1f}% under benchmark. Verify scope and quality compliance.",
                "severity": "warning",
                "action_items": [
                    "Verify specification compliance",
                    "Check material grade",
                    "Audit scope inclusion",
                ],
            },
            "medium_over": {
                "text": "REVIEW: {item} is {pct:.1f}% above benchmark. Monitor and negotiate.",
                "severity": "medium",
                "action_items": ["Request quote breakdown", "Compare with market index"],
            },
            "medium_under": {
                "text": "NOTE: {item} is {pct:.1f}% below benchmark. Confirm quality.",
                "severity": "low",
                "action_items": [
                    "Confirm material specification",
                    "Check labor inclusion",
                ],
            },
        }

        recs = []
        for v in variances:
            if v["severity"] == "low":
                continue
            direction = "over" if v["variance_pct"] > 0 else "under"
            tpl = templates.get(f"{v['severity']}_{direction}", templates["medium_over"])
            recs.append(
                {
                    "item_key": v["item_key"],
                    "recommendation": tpl["text"].format(
                        item=v["description"], pct=abs(v["variance_pct"])
                    ),
                    "severity": tpl["severity"],
                    "action_items": tpl["action_items"],
                    "variance_pct": v["variance_pct"],
                }
            )
        return recs
