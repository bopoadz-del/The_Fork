"""Beam analysis (statics / demand) — additive library, drop-catalog gap-fill.

Internal forces on a single beam from loads. These are DEMAND, not capacity, and
are **code-agnostic** — statics is the same under ACI or Eurocode. Capacity /
design lives in construction_formulas_structural_rc.py (dual-code). Keeping the
two apart is deliberate: demand != capacity.

Sign convention: sagging moment positive; all outputs in kN and kN.m for
w in kN/m, P in kN, spans in m.
"""
from __future__ import annotations


def beam_moment_simple(udl_w_kn_m: float, span_m: float) -> dict:
    """Simply-supported beam under a uniform load: Mmax = w*L^2/8 at midspan.
    (This is also the "uniform load" case — beam_moment_uniform is the same
    statics; the fixed-end variant is beam_moment_fixed_udl.)"""
    w = float(udl_w_kn_m)
    L = float(span_m)
    m = w * L * L / 8.0
    return {
        "max_moment_kn_m": round(m, 3),
        "location": "midspan",
        "standard": "statics (SS, UDL)",
        "note": f"Mmax = w*L^2/8 = {w}*{L}^2/8 = {m:.3f} kN.m at midspan.",
    }


def beam_moment_point_load(
    point_load_kn: float,
    span_m: float,
    distance_from_left_m: float = None,
) -> dict:
    """Simply-supported beam under a single point load. Central: M = P*L/4.
    Off-centre at a from the left support: M = P*a*b/L (b = L-a), under the load."""
    P = float(point_load_kn)
    L = float(span_m)
    if distance_from_left_m is None:
        m = P * L / 4.0
        expr = f"P*L/4 = {P}*{L}/4"
        loc = "midspan"
    else:
        a = float(distance_from_left_m)
        b = L - a
        m = P * a * b / L
        expr = f"P*a*b/L = {P}*{a}*{b}/{L}"
        loc = f"under load at {a} m"
    return {
        "max_moment_kn_m": round(m, 3),
        "location": loc,
        "standard": "statics (SS, point load)",
        "note": f"M = {expr} = {m:.3f} kN.m ({loc}).",
    }


def beam_shear_simple(
    udl_w_kn_m: float = 0.0,
    span_m: float = 0.0,
    central_point_load_kn: float = 0.0,
) -> dict:
    """Max shear at the support of a simply-supported beam: V = w*L/2 (+ P/2 for
    a central point load)."""
    w = float(udl_w_kn_m)
    L = float(span_m)
    P = float(central_point_load_kn)
    v = w * L / 2.0 + P / 2.0
    return {
        "max_shear_kn": round(v, 3),
        "location": "support",
        "standard": "statics (SS)",
        "note": f"Vmax = w*L/2 + P/2 = {w}*{L}/2 + {P}/2 = {v:.3f} kN at support.",
    }


def beam_moment_fixed_udl(udl_w_kn_m: float, span_m: float) -> dict:
    """Beam fixed at both ends under a uniform load: hogging M = w*L^2/12 at the
    supports (the max), sagging M = w*L^2/24 at midspan."""
    w = float(udl_w_kn_m)
    L = float(span_m)
    m_support = w * L * L / 12.0
    m_mid = w * L * L / 24.0
    return {
        "support_moment_kn_m": round(m_support, 3),
        "midspan_moment_kn_m": round(m_mid, 3),
        "max_moment_kn_m": round(m_support, 3),
        "standard": "statics (fixed-fixed, UDL)",
        "note": (f"Support (hogging) = w*L^2/12 = {w}*{L}^2/12 = {m_support:.3f}; "
                 f"midspan (sagging) = w*L^2/24 = {m_mid:.3f} kN.m."),
    }


ADDITIONAL_CALCULATORS = {
    "beam_moment_simple": beam_moment_simple,
    "beam_moment_point_load": beam_moment_point_load,
    "beam_shear_simple": beam_shear_simple,
    "beam_moment_fixed_udl": beam_moment_fixed_udl,
}
