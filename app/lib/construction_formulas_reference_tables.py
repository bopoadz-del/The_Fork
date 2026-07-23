"""Reference-table calculators (additive library, gap-fill).

These are NOT derived physics — they return values from a cited factor/threshold
table or classify a caller-supplied spec. Each result carries ``kind =
"reference_table"`` and a note stating the source and its project/vendor caveat,
so a plausible-looking number is never mistaken for a derivation.
"""
from __future__ import annotations


# Indicative embodied-carbon factors (kgCO2e per m3 of concrete), ICE/EPD family.
# Real projects use the supplier's EPD — these are typical GGBS-free CEM I values.
_CONCRETE_ECO2_KGM3 = {"c20": 260.0, "c25": 290.0, "c30": 320.0,
                       "c35": 350.0, "c40": 390.0}


def carbon_footprint_concrete(
    volume_m3: float,
    grade: str = "c30",
    embodied_kgco2e_m3: float = None,
) -> dict:
    """Embodied CO2e of a concrete pour = volume * factor. Factor from a typical
    ICE/EPD table by grade, or pass ``embodied_kgco2e_m3`` from the supplier EPD."""
    g = (grade or "c30").strip().lower()
    factor = float(embodied_kgco2e_m3) if embodied_kgco2e_m3 is not None \
        else _CONCRETE_ECO2_KGM3.get(g, 320.0)
    total = float(volume_m3) * factor
    return {
        "kind": "reference_table",
        "total_kgco2e": round(total, 1),
        "total_tco2e": round(total / 1000.0, 3),
        "factor_kgco2e_m3": factor,
        "standard": "ICE / EPD embodied-carbon (indicative)",
        "note": (f"{volume_m3} m3 * {factor} kgCO2e/m3 = {total:.0f} kgCO2e. "
                 "Use the supplier EPD for a project figure; table is indicative."),
    }


# LEED v4 BD+C certification thresholds (points out of 110).
_LEED_LEVELS = [(80, "Platinum"), (60, "Gold"), (50, "Silver"), (40, "Certified")]


def leed_points_estimate(points: float) -> dict:
    """Map a LEED v4 point total to a certification level. Certified 40-49,
    Silver 50-59, Gold 60-79, Platinum 80+."""
    p = float(points)
    level = "Not certified"
    for threshold, name in _LEED_LEVELS:
        if p >= threshold:
            level = name
            break
    return {
        "kind": "reference_table",
        "points": p,
        "certification_level": level,
        "standard": "LEED v4 BD+C thresholds",
        "note": f"{p} points -> {level} (Certified>=40, Silver>=50, Gold>=60, Platinum>=80).",
    }


# Typical clash tolerance by BIM LOD (mm). Project BEP governs — indicative only.
_LOD_CLASH_MM = {100: None, 200: 50.0, 300: 25.0, 350: 12.0, 400: 6.0, 500: 3.0}


def bim_clash_tolerance(lod: int = 350) -> dict:
    """Typical hard-clash tolerance for a BIM level of development. This is a
    BEP convention, NOT a standard constant — confirm in the project BEP."""
    tol = _LOD_CLASH_MM.get(int(lod))
    return {
        "kind": "reference_table",
        "lod": int(lod),
        "clash_tolerance_mm": tol,
        "standard": "BIM BEP convention (indicative)",
        "note": (f"LOD {lod}: typical hard-clash tolerance "
                 f"{'undefined' if tol is None else f'±{tol} mm'}. "
                 "Project BEP governs; not a standard constant."),
    }


def laser_scan_accuracy(range_m: float, accuracy_at_10m_mm: float = 2.0) -> dict:
    """Scale a scanner's stated ranging accuracy to a working range (linear
    approximation). Pass the accuracy from the scanner datasheet; the value is
    vendor/instrument-specific, not a derived constant."""
    r = float(range_m)
    acc = float(accuracy_at_10m_mm) * (r / 10.0)
    return {
        "kind": "reference_table",
        "range_m": r,
        "estimated_accuracy_mm": round(acc, 2),
        "standard": "scanner datasheet (vendor-specific)",
        "note": (f"{accuracy_at_10m_mm} mm at 10 m scaled to {r} m ~= {acc:.2f} mm "
                 "(linear approx.; use the instrument datasheet)."),
    }


ADDITIONAL_CALCULATORS = {
    "carbon_footprint_concrete": carbon_footprint_concrete,
    "leed_points_estimate": leed_points_estimate,
    "bim_clash_tolerance": bim_clash_tolerance,
    "laser_scan_accuracy": laser_scan_accuracy,
}
