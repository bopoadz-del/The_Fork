"""SymPy Reasoning Block — symbolic variance analysis + construction recommendations.

This block lives up to its name: it builds real symbolic expressions for
each metric using sympy.symbols, lets sympy simplify them once at module
import, then substitutes the per-item numerics in to produce both:
- the closed-form formula string (so the caller can see the math)
- the evaluated numeric value

A previous version of this block wrapped every float in ``sp.Float()`` then
immediately unwrapped via ``float()`` with no symbolic manipulation in
between, which made the "symbolic" naming a lie. The current version emits
formulas alongside numbers so a user can see exactly what was computed.
"""

from typing import Any, Dict, List, Tuple
from app.core.universal_base import UniversalBlock

# Symbolic expressions built once at import time. These are the closed-form
# formulas the block uses; they're exposed to the caller in the output so the
# computation is auditable.
try:
    import sympy as _sp
    _SP_AVAILABLE = True
except ImportError:  # pragma: no cover — sympy is in requirements.txt
    _sp = None
    _SP_AVAILABLE = False

if _SP_AVAILABLE:
    _ACTUAL, _AVG, _STD, _QTY = _sp.symbols(
        "actual avg std_dev quantity", real=True
    )
    # variance_pct = (actual - avg) / avg * 100   — simplified canonical form
    _VARIANCE_PCT_EXPR = _sp.simplify((_ACTUAL - _AVG) / _AVG * 100)
    # z_score        = (actual - avg) / std_dev
    _Z_SCORE_EXPR = _sp.simplify((_ACTUAL - _AVG) / _STD)
    # cost_impact    = (actual - avg) * quantity
    _COST_IMPACT_EXPR = _sp.simplify((_ACTUAL - _AVG) * _QTY)
    # Formula strings — exposed in output so the caller sees the math.
    _VARIANCE_PCT_STR = str(_VARIANCE_PCT_EXPR)
    _Z_SCORE_STR = str(_Z_SCORE_EXPR)
    _COST_IMPACT_STR = str(_COST_IMPACT_EXPR)
else:
    _VARIANCE_PCT_STR = "(actual - avg) / avg * 100"
    _Z_SCORE_STR = "(actual - avg) / std_dev"
    _COST_IMPACT_STR = "(actual - avg) * quantity"


def _eval_symbolic(expr, subs: Dict) -> float:
    """Substitute numerics into a sympy expression and return a float.

    Falls back to plain Python arithmetic when sympy isn't available so the
    block is still callable in stripped environments (the formula strings
    above are correct either way).
    """
    if _SP_AVAILABLE:
        return float(expr.subs(subs))
    # Pure-Python fallback. Limited to the three expressions above.
    a, v = subs.get(_ACTUAL, 0), subs.get(_AVG, 0)
    s = subs.get(_STD, 0)
    q = subs.get(_QTY, 1)
    if expr is _VARIANCE_PCT_EXPR:
        return (a - v) / v * 100 if v else 0.0
    if expr is _Z_SCORE_EXPR:
        return (a - v) / s if s else 0.0
    if expr is _COST_IMPACT_EXPR:
        return (a - v) * q
    raise RuntimeError("Unknown expression")


class SymPyReasoningBlock(UniversalBlock):
    name = "sympy_reasoning"
    version = "1.1.0"
    description = (
        "Symbolic variance analysis: builds real sympy expressions for "
        "variance %, z-score, and cost impact; emits both the formula "
        "strings and the evaluated values."
    )
    layer = 3
    tags = ["domain", "construction", "reasoning", "math", "symbolic", "sympy"]
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
                {"name": "formulas", "type": "json", "label": "Formulas"},
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

        if not _SP_AVAILABLE:
            return {"status": "error", "error": "sympy not installed. Run: pip install sympy"}

        threshold = float(
            params.get("variance_threshold_pct",
                        self.config.get("variance_threshold_pct", 10.0))
        )

        variances = self._compute_variances(boq_data, historical_benchmarks, threshold)
        cost_impacts = self._compute_cost_impacts(variances)
        recommendations = self._generate_recommendations(variances, spec_data, drawing_data)

        return {
            "status": "success",
            "recommendations": recommendations,
            "variances": variances,
            "cost_impacts": cost_impacts,
            "items_analyzed": len(boq_data),
            "high_variance_count": sum(1 for v in variances if v.get("severity") == "high"),
            # Expose the closed-form formulas the block evaluated. This makes
            # the math auditable — anyone reading the response can verify the
            # block isn't doing something different from what its name suggests.
            "formulas": {
                "variance_pct": _VARIANCE_PCT_STR,
                "z_score": _Z_SCORE_STR,
                "cost_impact": _COST_IMPACT_STR,
            },
        }

    def _compute_variances(
        self, boq_data: List, benchmarks: Dict, threshold: float
    ) -> List[Dict]:
        variances = []
        for item in boq_data:
            key = (
                item.get("item_key")
                or item.get("description", "").lower().replace(" ", "_")
            )
            # An item with `unit_cost: 0` was previously silently falling
            # through to `rate` — explicit None checks now disambiguate.
            actual_raw = item.get("unit_cost")
            if actual_raw is None:
                actual_raw = item.get("rate", 0)
            actual = float(actual_raw or 0)
            benchmark = benchmarks.get(key, {})
            avg_cost = float(benchmark.get("avg_cost", 0))
            std_dev = float(benchmark.get("std_dev", 0))

            if avg_cost <= 0:
                continue

            subs = {_ACTUAL: actual, _AVG: avg_cost, _STD: std_dev}
            variance_pct = _eval_symbolic(_VARIANCE_PCT_EXPR, subs)
            z_score = _eval_symbolic(_Z_SCORE_EXPR, subs) if std_dev > 0 else 0.0

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

    def _compute_cost_impacts(self, variances: List[Dict]) -> List[Dict]:
        impacts = []
        for v in variances:
            subs = {
                _ACTUAL: float(v.get("actual_cost", 0)),
                _AVG: float(v.get("benchmark_avg", 0)),
                _QTY: float(v.get("quantity", 1)),
            }
            impact = _eval_symbolic(_COST_IMPACT_EXPR, subs)
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
