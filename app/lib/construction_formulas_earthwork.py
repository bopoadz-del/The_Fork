"""Earthwork / geotech calculators (additive library, drop-catalog gap-fill).

Geometry + soil-state factors (bulking/swell, compaction) and an infinite-slope
factor of safety. Code-agnostic. Factors are parameters; arithmetic in ``note``.
Volume states: BANK (in situ), LOOSE (excavated, bulked), COMPACTED (placed).
"""
from __future__ import annotations

import math


def excavation_volume(
    length_m: float,
    width_m: float,
    depth_m: float,
    bulking_factor: float = 0.25,
) -> dict:
    """Bank (in-situ) excavation volume L*W*D, and the loose (bulked) volume for
    haulage = bank*(1+bulking)."""
    bank = float(length_m) * float(width_m) * float(depth_m)
    loose = bank * (1.0 + bulking_factor)
    return {
        "bank_volume_m3": round(bank, 3),
        "loose_volume_m3": round(loose, 3),
        "bulking_factor": bulking_factor,
        "standard": "geometry / soil bulking",
        "note": (f"Bank = {length_m}*{width_m}*{depth_m} = {bank:.2f} m3; "
                 f"loose = bank*(1+{bulking_factor}) = {loose:.2f} m3 (haulage)."),
    }


def backfill_volume(
    excavation_bank_m3: float,
    structure_volume_m3: float,
    swell_factor: float = 0.20,
) -> dict:
    """Backfill needed to fill the void around a structure. Void (compacted) =
    excavation - structure; loose material to import = void*(1+swell)."""
    void = float(excavation_bank_m3) - float(structure_volume_m3)
    void = max(void, 0.0)
    loose_needed = void * (1.0 + swell_factor)
    return {
        "void_volume_m3": round(void, 3),
        "loose_backfill_needed_m3": round(loose_needed, 3),
        "swell_factor": swell_factor,
        "standard": "geometry / soil swell",
        "note": (f"Void = {excavation_bank_m3} - {structure_volume_m3} = "
                 f"{void:.2f} m3; loose to import = void*(1+{swell_factor}) = "
                 f"{loose_needed:.2f} m3."),
    }


def cut_fill_balance(
    cut_volume_m3: float,
    fill_volume_m3: float,
    bulking_factor: float = 0.25,
) -> dict:
    """Site earthwork balance in bank m3. balance = cut - fill; positive = surplus
    to export, negative = deficit to import. Loose export/import shown for haulage."""
    cut = float(cut_volume_m3)
    fill = float(fill_volume_m3)
    balance = cut - fill
    status = "export_surplus" if balance > 0 else ("import_deficit" if balance < 0 else "balanced")
    loose = abs(balance) * (1.0 + bulking_factor)
    return {
        "balance_bank_m3": round(balance, 3),
        "status": status,
        "haul_loose_m3": round(loose, 3),
        "standard": "earthwork mass balance",
        "note": (f"Balance = cut - fill = {cut} - {fill} = {balance:.1f} m3 "
                 f"({status}); loose to haul = |bal|*(1+{bulking_factor}) = {loose:.1f} m3."),
    }


def compaction_control(
    field_dry_density: float,
    max_dry_density: float,
    required_compaction_percent: float = 95.0,
) -> dict:
    """Field compaction = field MDD / lab MDD * 100. Pass if >= required (default
    95% Standard/Modified Proctor)."""
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


def slope_fos_simple(
    friction_angle_deg: float,
    slope_angle_deg: float,
    cohesion_kpa: float = 0.0,
    unit_weight_kn_m3: float = 18.0,
    depth_m: float = 0.0,
) -> dict:
    """Infinite-slope factor of safety (dry). Cohesionless: FoS = tan(phi)/tan(beta).
    With cohesion: FoS = c'/(gamma*z*sin(beta)*cos(beta)) + tan(phi)/tan(beta)."""
    phi = math.radians(float(friction_angle_deg))
    beta = math.radians(float(slope_angle_deg))
    frictional = math.tan(phi) / math.tan(beta)
    cohesive = 0.0
    if cohesion_kpa > 0 and depth_m > 0:
        cohesive = cohesion_kpa / (unit_weight_kn_m3 * depth_m
                                   * math.sin(beta) * math.cos(beta))
    fos = cohesive + frictional
    return {
        "factor_of_safety": round(fos, 3),
        "frictional_term": round(frictional, 3),
        "cohesive_term": round(cohesive, 3),
        "standard": "infinite-slope stability",
        "note": (f"tan(phi)/tan(beta) = tan{friction_angle_deg}/tan{slope_angle_deg} "
                 f"= {frictional:.3f}; cohesive term = {cohesive:.3f}; FoS = {fos:.3f}."),
    }


ADDITIONAL_CALCULATORS = {
    "excavation_volume": excavation_volume,
    "backfill_volume": backfill_volume,
    "cut_fill_balance": cut_fill_balance,
    "compaction_control": compaction_control,
    "slope_fos_simple": slope_fos_simple,
}
