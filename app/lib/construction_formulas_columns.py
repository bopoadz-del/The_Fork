"""RC column axial capacity WITH slenderness (additive library).

Dual-code ACI 318-19 / EN 1992-1-1. Reports the short-column design axial
capacity, the slenderness ratio and short/slender classification, the Euler
critical buckling load (ACI) or the EC2 slenderness limit, and — when an applied
load is supplied — the ACI moment magnifier. A single-check tool, not a full
biaxial slender-column design. Arithmetic shown in ``note``.
"""
from __future__ import annotations

import math

_ACI = "aci"
_EC = "eurocode"


def _norm_code(code: str) -> str:
    c = (code or "").strip().lower()
    if c in ("ec", "eurocode", "en", "en1992"):
        return _EC
    return _ACI


def column_axial_capacity(
    gross_area_mm2: float,
    steel_area_mm2: float,
    fc_mpa: float,
    fy_mpa: float,
    unsupported_length_mm: float,
    radius_gyration_mm: float,
    code: str = _ACI,
    tie_type: str = "tied",
    k_factor: float = 1.0,
    applied_load_kn: float = None,
    cm: float = 1.0,
    slenderness_limit: float = 22.0,
) -> dict:
    """RC column axial capacity with a slenderness assessment.

    ACI 318-19 §22.4 / §6.6.4: P0 = 0.85*fc*(Ag-Ast) + fy*Ast;
    Pn,max = alpha*P0 (alpha=0.80 tied / 0.85 spiral); phi.Pn = phi*Pn,max
    (phi=0.65 tied / 0.75 spiral). Slenderness ratio = k*lu/r; Euler
    Pc = pi^2*EI/(k*lu)^2, EI = 0.4*Ec*Ig, Ec=4700*sqrt(fc), Ig=Ag*r^2;
    magnifier delta_ns = Cm/(1 - Pu/(0.75*Pc)) >= 1.0.
    EN 1992-1-1 §6.1/§5.8: NRd = fcd*(Ag-As) + fyd*As; slenderness lambda = l0/i;
    lambda_lim = 20*A*B*C/sqrt(n), n = NEd/(Ac*fcd).
    """
    cc = _norm_code(code)
    Ag, Ast = float(gross_area_mm2), float(steel_area_mm2)
    Ac = Ag - Ast
    lu, r, k = float(unsupported_length_mm), float(radius_gyration_mm), float(k_factor)
    klu = k * lu
    slenderness = klu / r
    out = {"code": cc, "slenderness_ratio": round(slenderness, 2)}

    if cc == _EC:
        fcd, fyd = fc_mpa / 1.5, fy_mpa / 1.15
        nrd_n = fcd * Ac + fyd * Ast
        out["axial_capacity_kn"] = round(nrd_n / 1000.0, 1)
        out["standard"] = "EN 1992-1-1 §6.1/§5.8"
        note = (f"NRd = fcd*Ac + fyd*As = {fcd:.1f}*{Ac:.0f} + {fyd:.1f}*{Ast:.0f} "
                f"= {nrd_n/1000.0:.1f} kN; lambda = l0/i = {klu:.0f}/{r} = {slenderness:.2f}")
        if applied_load_kn is not None:
            n = (float(applied_load_kn) * 1000.0) / (Ac * fcd)
            lam_lim = 20.0 * 0.7 * 1.1 * 0.7 / math.sqrt(n) if n > 0 else float("inf")
            out["slenderness_limit"] = round(lam_lim, 2)
            out["is_slender"] = slenderness > lam_lim
            note += f"; lambda_lim = 20*A*B*C/sqrt(n) = {lam_lim:.2f} ({'slender' if slenderness>lam_lim else 'short'})"
        else:
            out["is_slender"] = slenderness > slenderness_limit
        out["note"] = note + "."
        return out

    # ACI
    alpha = 0.85 if tie_type.strip().lower() == "spiral" else 0.80
    phi = 0.75 if tie_type.strip().lower() == "spiral" else 0.65
    p0 = 0.85 * fc_mpa * Ac + fy_mpa * Ast
    pn_max = alpha * p0
    phi_pn = phi * pn_max
    ec = 4700.0 * math.sqrt(fc_mpa)
    ig = Ag * r * r
    ei = 0.4 * ec * ig
    pc = math.pi ** 2 * ei / (klu ** 2)
    out["axial_capacity_kn"] = round(phi_pn / 1000.0, 1)
    out["euler_critical_load_kn"] = round(pc / 1000.0, 1)
    out["is_slender"] = slenderness > slenderness_limit
    out["standard"] = "ACI 318-19 §22.4/§6.6.4"
    note = (f"P0 = 0.85*fc*Ac + fy*Ast = {p0/1000.0:.0f} kN; Pn,max = {alpha}*P0; "
            f"phi.Pn = {phi}*Pn,max = {phi_pn/1000.0:.1f} kN. "
            f"slenderness = k*lu/r = {klu:.0f}/{r} = {slenderness:.2f} "
            f"({'slender' if out['is_slender'] else 'short'} vs {slenderness_limit}); "
            f"Pc = pi^2*EI/(k*lu)^2 = {pc/1000.0:.0f} kN")
    if applied_load_kn is not None:
        pu = float(applied_load_kn) * 1000.0
        denom = 1.0 - pu / (0.75 * pc)
        delta = max(cm / denom, 1.0) if denom > 0 else float("inf")
        out["moment_magnifier_delta_ns"] = round(delta, 3)
        note += f"; delta_ns = Cm/(1 - Pu/(0.75*Pc)) = {delta:.3f}"
    out["note"] = note + "."
    return out


ADDITIONAL_CALCULATORS = {
    "column_axial_capacity": column_axial_capacity,
}
