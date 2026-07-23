"""Reinforced-concrete DESIGN (capacity) — additive library, drop-catalog gap-fill.

Dual-code: ``code`` = "aci" (ACI 318-19) or "eurocode" (EN 1992-1-1). These are
CAPACITY / design checks (distinct from the code-agnostic demand statics in
construction_formulas_beam_analysis.py). Material partial factors follow each
code; the arithmetic is shown in ``note``. Simplified single-check tools, not a
full member design (no detailing / serviceability limit states).
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


def rc_beam_moment_capacity(
    steel_area_mm2: float,
    fy_mpa: float,
    width_mm: float,
    eff_depth_mm: float,
    fc_mpa: float,
    code: str = _ACI,
) -> dict:
    """Design flexural capacity of a singly-reinforced rectangular RC beam.

    ACI 318-19: a = As*fy/(0.85*fc*b); phi.Mn = 0.9*As*fy*(d - a/2).
    EN 1992-1-1: fcd=fc/1.5, fyd=fy/1.15; x = As*fyd/(0.8*b*fcd); z = d - 0.4x;
    MRd = As*fyd*z.
    """
    cc = _norm_code(code)
    As, b, d = float(steel_area_mm2), float(width_mm), float(eff_depth_mm)
    if cc == _EC:
        fcd, fyd = fc_mpa / 1.5, fy_mpa / 1.15
        x = As * fyd / (0.8 * b * fcd)
        z = d - 0.4 * x
        m_nmm = As * fyd * z
        std = "EN 1992-1-1 §6.1 (gc=1.5, gs=1.15)"
        note = (f"fcd={fcd:.1f}, fyd={fyd:.1f}; x=As*fyd/(0.8*b*fcd)={x:.1f} mm; "
                f"z=d-0.4x={z:.1f} mm; MRd=As*fyd*z={m_nmm/1e6:.2f} kN.m")
        cap = m_nmm / 1e6
    else:
        a = As * fy_mpa / (0.85 * fc_mpa * b)
        m_nmm = 0.9 * As * fy_mpa * (d - a / 2.0)
        std = "ACI 318-19 §22.2 (phi=0.9)"
        note = (f"a=As*fy/(0.85*fc*b)={a:.1f} mm; "
                f"phi.Mn=0.9*As*fy*(d-a/2)={m_nmm/1e6:.2f} kN.m")
        cap = m_nmm / 1e6
    return {
        "code": cc,
        "moment_capacity_kn_m": round(cap, 2),
        "standard": std,
        "note": note,
    }


def rc_beam_shear_capacity(
    width_mm: float,
    eff_depth_mm: float,
    fc_mpa: float,
    code: str = _ACI,
    rho_l: float = 0.01,
) -> dict:
    """Concrete shear capacity (no shear reinforcement).

    ACI 318-19 §22.5: phi.Vc = 0.75 * 0.17 * sqrt(fc) * bw * d (SI, N).
    EN 1992-1-1 §6.2.2: VRd,c = CRd,c*k*(100*rho_l*fck)^(1/3)*bw*d,
    CRd,c=0.12, k=1+sqrt(200/d)<=2.0, rho_l<=0.02.
    """
    cc = _norm_code(code)
    bw, d = float(width_mm), float(eff_depth_mm)
    if cc == _EC:
        k = min(1.0 + math.sqrt(200.0 / d), 2.0)
        rho = min(rho_l, 0.02)
        vrd_n = 0.12 * k * (100.0 * rho * fc_mpa) ** (1.0 / 3.0) * bw * d
        std = "EN 1992-1-1 §6.2.2 (CRd,c=0.12)"
        note = (f"k=1+sqrt(200/d)={k:.3f}; VRd,c=0.12*k*(100*rho*fck)^(1/3)*bw*d "
                f"= {vrd_n/1000.0:.2f} kN")
        cap = vrd_n / 1000.0
    else:
        vc_n = 0.75 * 0.17 * math.sqrt(fc_mpa) * bw * d
        std = "ACI 318-19 §22.5 (phi=0.75)"
        note = (f"phi.Vc=0.75*0.17*sqrt(fc)*bw*d=0.75*0.17*{math.sqrt(fc_mpa):.3f}"
                f"*{bw}*{d} = {vc_n/1000.0:.2f} kN")
        cap = vc_n / 1000.0
    return {
        "code": cc,
        "shear_capacity_kn": round(cap, 2),
        "standard": std,
        "note": note,
    }


# Span/depth (L/ratio) minimum-thickness ratios by support condition.
_ACI_RATIOS = {"simply_supported": 20.0, "one_end_continuous": 24.0,
               "both_ends_continuous": 28.0, "cantilever": 10.0}
_EC_RATIOS = {"simply_supported": 20.0, "one_end_continuous": 26.0,
              "both_ends_continuous": 30.0, "cantilever": 8.0}


def slab_thickness_min(
    span_mm: float,
    support_condition: str = "simply_supported",
    code: str = _ACI,
    fy_mpa: float = 420.0,
) -> dict:
    """Minimum one-way slab thickness for deflection control (span/ratio).

    ACI 318-19 Table 7.3.1.1 (fy=420): SS L/20, one-end L/24, both-ends L/28,
    cantilever L/10; x(0.4 + fy/700) for other fy.
    EN 1992-1-1 §7.4.2 basic span/depth: SS 20, end 26, interior 30, cantilever 8.
    """
    cc = _norm_code(code)
    L = float(span_mm)
    sc = (support_condition or "simply_supported").strip().lower()
    if cc == _EC:
        ratio = _EC_RATIOS.get(sc, 20.0)
        t = L / ratio
        std = "EN 1992-1-1 §7.4.2 (basic span/depth)"
        note = f"L/{ratio:.0f} = {L}/{ratio:.0f} = {t:.1f} mm"
    else:
        ratio = _ACI_RATIOS.get(sc, 20.0)
        fy_mod = 0.4 + fy_mpa / 700.0
        t = (L / ratio) * fy_mod
        std = "ACI 318-19 Table 7.3.1.1"
        note = (f"L/{ratio:.0f} = {L/ratio:.1f} mm; x(0.4+fy/700)={fy_mod:.3f} "
                f"-> {t:.1f} mm")
    return {
        "code": cc,
        "min_thickness_mm": round(t, 1),
        "support_condition": sc,
        "standard": std,
        "note": note,
    }


def rebar_lap_length(
    bar_diameter_mm: float,
    fy_mpa: float,
    fc_mpa: float,
    code: str = _ACI,
    confinement_ratio: float = 1.5,
) -> dict:
    """Tension lap-splice length for a deformed bar.

    ACI 318-19 §25.4.2: ld = (fy/(1.1*sqrt(fc)*((cb+Ktr)/db)))*db (psi factors=1);
    Class B lap = 1.3*ld. ``confinement_ratio`` = (cb+Ktr)/db, capped 2.5.
    EN 1992-1-1 §8.4/8.7: lb,rqd = (db/4)*(fyd/fbd), fbd=2.25*fctd,
    fctd=0.7*0.3*fck^(2/3)/1.5; lap l0 = 1.5*lb,rqd (>50% lapped).
    """
    cc = _norm_code(code)
    db = float(bar_diameter_mm)
    if cc == _EC:
        fyd = fy_mpa / 1.15
        fctm = 0.3 * fc_mpa ** (2.0 / 3.0)
        fctd = 0.7 * fctm / 1.5
        fbd = 2.25 * fctd
        lb_rqd = (db / 4.0) * (fyd / fbd)
        lap = 1.5 * lb_rqd
        std = "EN 1992-1-1 §8.4/8.7"
        note = (f"fbd=2.25*0.7*0.3*fck^(2/3)/1.5={fbd:.3f} MPa; "
                f"lb,rqd=(db/4)*(fyd/fbd)={lb_rqd:.1f} mm; l0=1.5*lb,rqd={lap:.1f} mm")
    else:
        conf = min(confinement_ratio, 2.5)
        ld = (fy_mpa / (1.1 * math.sqrt(fc_mpa) * conf)) * db
        lap = 1.3 * ld
        std = "ACI 318-19 §25.4.2 (Class B lap)"
        note = (f"ld=(fy/(1.1*sqrt(fc)*{conf}))*db={ld:.1f} mm; "
                f"lap=1.3*ld={lap:.1f} mm")
    return {
        "code": cc,
        "lap_length_mm": round(lap, 1),
        "standard": std,
        "note": note,
    }


ADDITIONAL_CALCULATORS = {
    "rc_beam_moment_capacity": rc_beam_moment_capacity,
    "rc_beam_shear_capacity": rc_beam_shear_capacity,
    "slab_thickness_min": slab_thickness_min,
    "rebar_lap_length": rebar_lap_length,
}
