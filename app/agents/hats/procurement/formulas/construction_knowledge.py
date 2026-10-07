"""Formulas from app.core.construction_knowledge owned by the procurement hat.

Moved unchanged by scripts/move_formulas.py (F-DRIVER Phase A).
"""
from __future__ import annotations
from app.lib.formula_registry import formula
from typing import Dict, List, Optional


@formula(
    owner='procurement',
    description='Scores and ranks tender submissions on weighted technical and commercial criteria.',
    inputs={'tenderers': '-', 'weights': '-'},
    outputs={'ranked_tenderers': '-', 'recommended': '-', 'weights_applied': '-', 'procedure': '-'},
)
def evaluate_tender(
    tenderers: List[Dict],
    weights: Optional[Dict] = None,
) -> Dict:
    """
    Score and rank tender submissions per the tender analysis procedure.

    tenderers: list of dicts, each with:
        {
            "name": str,
            "technical_score": float (0-100),
            "commercial_score": float (0-100),
            "hse_score": float (0-100),
            "local_content_score": float (0-100),  # optional
        }

    weights: optional dict overriding defaults:
        {"technical": 0.45, "commercial": 0.45, "hse": 0.07, "local_content": 0.03}
    """
    if not tenderers:
        return {"error": "evaluate_tender requires at least one tenderer."}
    if weights is None:
        weights = {"technical": 0.45, "commercial": 0.45, "hse": 0.07, "local_content": 0.03}

    scored = []
    for t in tenderers:
        total = (
            t.get("technical_score", 0) * weights["technical"]
            + t.get("commercial_score", 0) * weights["commercial"]
            + t.get("hse_score", 0) * weights["hse"]
            + t.get("local_content_score", 0) * weights.get("local_content", 0)
        )
        scored.append({**t, "weighted_total": round(total, 2)})

    ranked = sorted(scored, key=lambda x: x["weighted_total"], reverse=True)
    for i, r in enumerate(ranked):
        r["rank"] = i + 1

    return {
        "ranked_tenderers": ranked,
        "recommended": ranked[0] if ranked else None,
        "weights_applied": weights,
        "procedure": "tender_analysis",
    }
