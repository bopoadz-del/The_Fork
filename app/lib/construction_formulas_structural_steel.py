"""Structural steel capacity calculators (additive library, drop-catalog gap-fill).

Dual-code: each formula takes ``code`` = "aci" (AISC 360-16 LRFD) or "eurocode"
(EN 1993). Deterministic tools, not a design suite: they compute a single
member/connection capacity from the governing limit states, with allowables
(Fy/Fu, weld/bolt strengths) passed as parameters. The arithmetic is shown in
``note`` so a reviewer can check every step by hand.

Standards: AISC 360-16 (Ch. D tension, Ch. J connections); EN 1993-1-1 §6.2.3
(tension), EN 1993-1-8 §3.6 (bolts) / §4.5.3 (welds).
"""
from __future__ import annotations

import math
from typing import Optional

_ACI = "aci"
_EC = "eurocode"


def _norm_code(code: str) -> str:
    c = (code or "").strip().lower()
    if c in ("ec", "eurocode", "en", "en1993"):
        return _EC
    return _ACI  # default AISC/ACI family


def steel_tension_capacity(
    gross_area_mm2: float,
    net_area_mm2: Optional[float] = None,
    fy_mpa: float = 345.0,
    fu_mpa: float = 450.0,
    code: str = _ACI,
) -> dict:
    """Design tensile strength of a steel member — the lesser of gross-section
    yielding and net-section rupture.

    AISC 360-16 §D2 (LRFD): yield phi.Pn = 0.90*Fy*Ag; rupture phi.Pn = 0.75*Fu*Ae.
    EN 1993-1-1 §6.2.3: Npl,Rd = A*fy/gM0 (gM0=1.0); Nu,Rd = 0.9*Anet*fu/gM2 (gM2=1.25).
    Governing capacity = min(yield, rupture).
    """
    cc = _norm_code(code)
    net = float(net_area_mm2) if net_area_mm2 is not None else float(gross_area_mm2)
    ag = float(gross_area_mm2)
    if cc == _EC:
        yield_n = ag * fy_mpa / 1.0
        rupture_n = 0.9 * net * fu_mpa / 1.25
        std = "EN 1993-1-1 §6.2.3 (gM0=1.0, gM2=1.25)"
        y_expr = f"A*fy/gM0 = {ag}*{fy_mpa}/1.0"
        r_expr = f"0.9*Anet*fu/gM2 = 0.9*{net}*{fu_mpa}/1.25"
    else:
        yield_n = 0.90 * fy_mpa * ag
        rupture_n = 0.75 * fu_mpa * net
        std = "AISC 360-16 §D2 (LRFD, phi_y=0.90, phi_r=0.75)"
        y_expr = f"0.90*Fy*Ag = 0.90*{fy_mpa}*{ag}"
        r_expr = f"0.75*Fu*Ae = 0.75*{fu_mpa}*{net}"
    yield_kn = yield_n / 1000.0
    rupture_kn = rupture_n / 1000.0
    governs = "yield" if yield_kn <= rupture_kn else "rupture"
    cap_kn = min(yield_kn, rupture_kn)
    return {
        "code": cc,
        "capacity_kn": round(cap_kn, 2),
        "governing_limit_state": governs,
        "yield_capacity_kn": round(yield_kn, 2),
        "rupture_capacity_kn": round(rupture_kn, 2),
        "standard": std,
        "note": (
            f"Yield: {y_expr} = {yield_kn:.1f} kN. "
            f"Rupture: {r_expr} = {rupture_kn:.1f} kN. "
            f"Governs: {governs} -> {cap_kn:.1f} kN."
        ),
    }


def bolt_shear_capacity(
    bolt_area_mm2: float,
    shear_strength_mpa: float = 372.0,
    n_shear_planes: int = 1,
    code: str = _ACI,
    alpha_v: float = 0.6,
) -> dict:
    """Design shear strength of ONE bolt (all shear planes).

    AISC 360-16 §J3.6 (LRFD): phi.Rn = 0.75 * Fnv * Ab * n_planes, Fnv = nominal
    shear strength (e.g. A325-N ~372 MPa, A325-X ~469 MPa).
    EN 1993-1-8 Table 3.4: Fv,Rd = alpha_v * fub * A * n_planes / gM2 (gM2=1.25),
    alpha_v = 0.6 (classes 4.6/5.6/8.8) or 0.5 (4.8/5.8/6.8/10.9). Here
    ``shear_strength_mpa`` carries fub for the Eurocode branch.
    """
    cc = _norm_code(code)
    ab = float(bolt_area_mm2)
    n = int(n_shear_planes)
    if cc == _EC:
        cap_n = alpha_v * shear_strength_mpa * ab * n / 1.25
        std = "EN 1993-1-8 Table 3.4 (gM2=1.25)"
        expr = f"alpha_v*fub*A*n/gM2 = {alpha_v}*{shear_strength_mpa}*{ab}*{n}/1.25"
    else:
        cap_n = 0.75 * shear_strength_mpa * ab * n
        std = "AISC 360-16 §J3.6 (LRFD, phi=0.75)"
        expr = f"0.75*Fnv*Ab*n = 0.75*{shear_strength_mpa}*{ab}*{n}"
    cap_kn = cap_n / 1000.0
    return {
        "code": cc,
        "capacity_kn": round(cap_kn, 2),
        "n_shear_planes": n,
        "standard": std,
        "note": f"{expr} = {cap_n:.0f} N = {cap_kn:.2f} kN (one bolt).",
    }


def weld_capacity(
    leg_size_mm: float,
    length_mm: float,
    electrode_strength_mpa: float = 490.0,
    code: str = _ACI,
    beta_w: float = 0.9,
) -> dict:
    """Design strength of a fillet weld over ``length_mm``.

    Effective throat = 0.707 * leg (equal-leg fillet).
    AISC 360-16 §J2.4 (LRFD): phi.Rn = 0.75 * (0.60*FEXX) * throat * L.
    EN 1993-1-8 §4.5.3.3 (simplified): Fw,Rd = fvw,d * throat * L,
    fvw,d = fu / (sqrt(3) * beta_w * gM2), gM2=1.25, beta_w correlation factor
    (0.8 S235 / 0.85 S275 / 0.9 S355).
    """
    cc = _norm_code(code)
    throat = 0.707 * float(leg_size_mm)
    L = float(length_mm)
    if cc == _EC:
        fvw_d = electrode_strength_mpa / (math.sqrt(3.0) * beta_w * 1.25)
        per_mm = fvw_d * throat
        std = "EN 1993-1-8 §4.5.3.3 (gM2=1.25)"
        expr = (f"fvw,d = fu/(sqrt3*beta_w*gM2) = {electrode_strength_mpa}/"
                f"(1.732*{beta_w}*1.25) = {fvw_d:.1f} MPa; x throat {throat:.3f}")
    else:
        per_mm = 0.75 * 0.60 * electrode_strength_mpa * throat
        std = "AISC 360-16 §J2.4 (LRFD, phi=0.75)"
        expr = (f"0.75*0.60*FEXX*throat = 0.75*0.60*{electrode_strength_mpa}*"
                f"{throat:.3f}")
    total_n = per_mm * L
    return {
        "code": cc,
        "capacity_kn": round(total_n / 1000.0, 2),
        "capacity_per_mm_kn": round(per_mm / 1000.0, 4),
        "effective_throat_mm": round(throat, 3),
        "standard": std,
        "note": (
            f"Throat = 0.707*{leg_size_mm} = {throat:.3f} mm. "
            f"{expr} = {per_mm:.1f} N/mm. x L={L} mm = {total_n/1000.0:.2f} kN."
        ),
    }


ADDITIONAL_CALCULATORS = {
    "steel_tension_capacity": steel_tension_capacity,
    "bolt_shear_capacity": bolt_shear_capacity,
    "weld_capacity": weld_capacity,
}
