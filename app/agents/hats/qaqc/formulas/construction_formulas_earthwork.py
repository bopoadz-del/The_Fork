"""Formulas from app.lib.construction_formulas_earthwork owned by the qaqc hat.

Moved unchanged by scripts/move_formulas.py (F-DRIVER Phase A).
"""
from __future__ import annotations
from app.lib.formula_registry import formula


@formula(
    owner='qaqc',
    description='Field compaction as a percentage of the laboratory maximum dry density, against the specified minimum.',
    inputs={'field_dry_density': '-', 'max_dry_density': '-', 'required_compaction_percent': '%'},
    outputs={'compaction_percent': '%', 'required_percent': '%', 'passed': '-'},
)
def compaction_control(
    field_dry_density: float,
    max_dry_density: float,
    required_compaction_percent: float = 95.0,
) -> dict:
    """Field compaction = field MDD / lab MDD * 100. Pass if >= required (default
    95% Standard/Modified Proctor)."""
    if float(max_dry_density) <= 0:
        return {"error": "max_dry_density must be > 0."}
    if float(field_dry_density) < 0:
        return {"error": "field_dry_density must be >= 0."}
    pct = float(field_dry_density) / float(max_dry_density) * 100.0
    passed = pct >= float(required_compaction_percent)
    return {
        "compaction_percent": round(pct, 2),
        "required_percent": required_compaction_percent,
        "passed": passed,
        "standard": "ASTM D698/D1557 (Proctor)",
        "note": (f"{field_dry_density}/{max_dry_density}*100 = {pct:.2f}% "
                 f"({'PASS' if passed else 'FAIL'} vs {required_compaction_percent}%)."),
    }
