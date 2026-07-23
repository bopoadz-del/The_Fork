"""Design-load calculators (additive library, drop-catalog gap-fill).

Dual-code: ``code`` = "aci" (US family: ASCE 7-16) or "eurocode" (EN 1991/1998).
Deterministic single-quantity tools with the standard's factors as parameters;
the arithmetic is shown in ``note`` for hand-checking.

Standards: ASCE 7-16 §26.10 (wind velocity pressure), §12.8 (seismic ELF),
§4.7 (live-load reduction); EN 1991-1-4 (wind), EN 1998-1 §4.3.3 (seismic),
EN 1991-1-1 §6.3.1.2 (live-load reduction).
"""
from __future__ import annotations

import math

_ACI = "aci"
_EC = "eurocode"


def _norm_code(code: str) -> str:
    c = (code or "").strip().lower()
    if c in ("ec", "eurocode", "en"):
        return _EC
    return _ACI


def wind_pressure(
    wind_speed_m_s: float,
    code: str = _ACI,
    kz: float = 1.0,
    kzt: float = 1.0,
    kd: float = 0.85,
    air_density_kg_m3: float = 1.25,
) -> dict:
    """Wind velocity pressure (Pa).

    ASCE 7-16 §26.10 (SI): qz = 0.613 * Kz * Kzt * Kd * V^2  (V in m/s -> Pa).
    EN 1991-1-4 §4.5: basic velocity pressure qb = 0.5 * rho * vb^2 (rho=1.25).
    (These are different reference quantities — ASCE includes exposure/directional
    factors; EC gives the basic pressure before exposure. Noted, not conflated.)
    """
    cc = _norm_code(code)
    v = float(wind_speed_m_s)
    if cc == _EC:
        q = 0.5 * air_density_kg_m3 * v * v
        std = "EN 1991-1-4 §4.5 (basic velocity pressure)"
        expr = f"0.5*rho*vb^2 = 0.5*{air_density_kg_m3}*{v}^2"
    else:
        q = 0.613 * kz * kzt * kd * v * v
        std = "ASCE 7-16 §26.10 (SI, qz)"
        expr = f"0.613*Kz*Kzt*Kd*V^2 = 0.613*{kz}*{kzt}*{kd}*{v}^2"
    return {
        "code": cc,
        "velocity_pressure_pa": round(q, 1),
        "velocity_pressure_kn_m2": round(q / 1000.0, 4),
        "standard": std,
        "note": f"{expr} = {q:.1f} Pa = {q/1000.0:.3f} kN/m2.",
    }


def seismic_base_shear(
    seismic_weight_kn: float,
    code: str = _ACI,
    sds: float = 1.0,
    r: float = 8.0,
    ie: float = 1.0,
    ag_g: float = 0.3,
    soil_factor_s: float = 1.2,
    behaviour_factor_q: float = 3.9,
    lambda_factor: float = 0.85,
) -> dict:
    """Equivalent lateral-force base shear V = Cs * W.

    ASCE 7-16 §12.8.1: Cs = SDS * Ie / R.
    EN 1998-1 §4.3.3.2: Cs = (ag/g * S * 2.5 / q) * lambda  (design spectral
    acceleration on the plateau, as a fraction of g).
    """
    cc = _norm_code(code)
    w = float(seismic_weight_kn)
    if cc == _EC:
        cs = (ag_g * soil_factor_s * 2.5 / behaviour_factor_q) * lambda_factor
        std = "EN 1998-1 §4.3.3.2 (plateau Sd, lambda)"
        expr = (f"Cs = (ag/g*S*2.5/q)*lambda = ({ag_g}*{soil_factor_s}*2.5/"
                f"{behaviour_factor_q})*{lambda_factor}")
    else:
        cs = sds * ie / r
        std = "ASCE 7-16 §12.8.1.1"
        expr = f"Cs = SDS*Ie/R = {sds}*{ie}/{r}"
    v = cs * w
    return {
        "code": cc,
        "base_shear_kn": round(v, 2),
        "seismic_response_coefficient_cs": round(cs, 4),
        "standard": std,
        "note": f"{expr} = {cs:.4f}. V = Cs*W = {cs:.4f}*{w} = {v:.1f} kN.",
    }


def live_load_reduction(
    base_live_load_kn_m2: float,
    tributary_area_m2: float,
    code: str = _ACI,
    kll: float = 4.0,
    psi0: float = 0.7,
    min_factor: float = 0.5,
) -> dict:
    """Reduced design live load on a member with a large tributary area.

    ASCE 7-16 §4.7.2 (SI): L = L0 * (0.25 + 4.57/sqrt(KLL*AT)), not less than
    min_factor*L0 (0.5 one floor / 0.4 multi). Reduction only when KLL*AT >= 37.2 m2.
    EN 1991-1-1 §6.3.1.2: alphaA = (5/7)*psi0 + A0/A <= 1.0 (A0 = 10 m2).
    """
    cc = _norm_code(code)
    l0 = float(base_live_load_kn_m2)
    at = float(tributary_area_m2)
    if cc == _EC:
        alpha = (5.0 / 7.0) * psi0 + 10.0 / at
        alpha = min(alpha, 1.0)
        reduced = alpha * l0
        std = "EN 1991-1-1 §6.3.1.2"
        note = (f"alphaA = (5/7)*psi0 + A0/A = 0.714*{psi0} + 10/{at} = "
                f"{alpha:.3f} (<=1.0). L = {alpha:.3f}*{l0} = {reduced:.3f} kN/m2.")
        factor = round(alpha, 3)
    else:
        if kll * at < 37.2:
            reduced = l0
            factor = 1.0
            std = "ASCE 7-16 §4.7.2 (no reduction: KLL*AT < 37.2 m2)"
            note = (f"KLL*AT = {kll}*{at} = {kll*at:.1f} < 37.2 m2 -> no reduction; "
                    f"L = L0 = {l0} kN/m2.")
        else:
            raw = 0.25 + 4.57 / math.sqrt(kll * at)
            factor = max(raw, min_factor)
            reduced = factor * l0
            std = "ASCE 7-16 §4.7.2 (SI, Eq. 4.7-1)"
            note = (f"L = L0*(0.25 + 4.57/sqrt(KLL*AT)) = {l0}*(0.25 + 4.57/"
                    f"sqrt({kll}*{at})) = {l0}*{raw:.3f}; floor {min_factor}*L0 -> "
                    f"factor {factor:.3f}; L = {reduced:.3f} kN/m2.")
    return {
        "code": cc,
        "reduced_live_load_kn_m2": round(reduced, 3),
        "reduction_factor": round(factor, 3),
        "standard": std,
        "note": note,
    }


ADDITIONAL_CALCULATORS = {
    "wind_pressure": wind_pressure,
    "seismic_base_shear": seismic_base_shear,
    "live_load_reduction": live_load_reduction,
}
