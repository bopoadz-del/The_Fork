"""Formulas from app.lib.construction_formulas_planning owned by the base package (every hat).

Moved unchanged by scripts/move_formulas.py (F-DRIVER Phase A).
"""
from __future__ import annotations
from app.lib.formula_registry import Param, formula


# Factors express "how many *to_unit* in one *from_unit*" via a common SI base.
_LENGTH_TO_M = {
    "m": 1.0,
    "meter": 1.0,
    "metre": 1.0,
    "ft": 0.3048,
    "feet": 0.3048,
    "foot": 0.3048,
}

_AREA_TO_M2 = {
    "m2": 1.0,
    "m²": 1.0,
    "sqm": 1.0,
    "ft2": 0.09290304,
    "ft²": 0.09290304,
    "sqft": 0.09290304,
    "sf": 0.09290304,
}

_VOLUME_TO_M3 = {
    "m3": 1.0,
    "m³": 1.0,
    "cum": 1.0,
    "ft3": 0.028316846592,
    "ft³": 0.028316846592,
    "cuft": 0.028316846592,
    "cf": 0.028316846592,
}

_TIME_TO_HOUR = {
    "h": 1.0,
    "hr": 1.0,
    "hour": 1.0,
    "hours": 1.0,
    "d": 24.0,
    "day": 24.0,
    "days": 24.0,
}

# Pressure and stress, in kPa. Construction asks for these constantly --
# concrete grades in MPa and psi, bearing capacity in kg/cm2, a specification
# in N/mm2 -- and until a live pressure-conversion ask there was no table, so every one was done by
# hand inside the answer. kg/cm2 is 0.0980665 MPa, NOT 0.1: dividing by ten is
# a 2% error that reads as rounding.
_PRESSURE_TO_KPA = {
    "pa": 0.001,
    "n/m2": 0.001,
    "n/m²": 0.001,
    "kpa": 1.0,
    "kn/m2": 1.0,
    "kn/m²": 1.0,
    "mpa": 1000.0,
    "n/mm2": 1000.0,
    "n/mm²": 1000.0,
    "mn/m2": 1000.0,
    "mn/m²": 1000.0,
    "gpa": 1_000_000.0,
    "kn/mm2": 1_000_000.0,
    "bar": 100.0,
    "mbar": 0.1,
    "atm": 101.325,
    "kg/cm2": 98.0665,
    "kg/cm²": 98.0665,
    "kgf/cm2": 98.0665,
    "ksc": 98.0665,
    "t/m2": 9.80665,
    "t/m²": 9.80665,
    "psi": 6.894757293168361,
    "lb/in2": 6.894757293168361,
    "psf": 0.04788025898033584,
    "lb/ft2": 0.04788025898033584,
    "ksi": 6894.757293168361,
    "kip/in2": 6894.757293168361,
}

@formula(
    owner='base',
    display_name='Unit conversion',
    description='Converts a value between common engineering units (length, area, volume, time, pressure and stress).',
    inputs={
        'value': Param('currency', -1e13, 1e13, label='value to convert'),
        'from_unit': Param('-'),
        'to_unit': Param('-'),
    },
    outputs={'value_in': '-', 'from_unit': '-', 'to_unit': '-', 'value_out': '-', 'dimension': '-', 'formula_used': '-'},
)
def pe_unit_convert(value: float, from_unit: str, to_unit: str) -> dict:
    """Common PE unit conversions (m↔ft, m²↔ft², m³↔ft³, day↔hour, and pressure/stress).

    Pressure covers Pa, kPa, MPa, GPa, N/mm², kN/m², bar, atm, kg/cm², t/m²,
    psi, psf and ksi -- the units a concrete grade, a bearing capacity or a
    specification is actually written in.
    """
    if value is None:
        return {"error": "pe_unit_convert requires value.", "required": ["value", "from_unit", "to_unit"]}
    src = (from_unit or "").strip().lower()
    dst = (to_unit or "").strip().lower()
    if not src or not dst:
        return {
            "error": "pe_unit_convert requires from_unit and to_unit.",
            "required": ["value", "from_unit", "to_unit"],
            "supported": sorted(
                set(_LENGTH_TO_M) | set(_AREA_TO_M2) | set(_VOLUME_TO_M3)
                | set(_TIME_TO_HOUR) | set(_PRESSURE_TO_KPA)
            ),
        }

    tables = (
        ("length", _LENGTH_TO_M),
        ("area", _AREA_TO_M2),
        ("volume", _VOLUME_TO_M3),
        ("time", _TIME_TO_HOUR),
        ("pressure", _PRESSURE_TO_KPA),
    )
    for kind, table in tables:
        if src in table and dst in table:
            base = float(value) * table[src]
            converted = base / table[dst]
            return {
                "value_in": float(value),
                "from_unit": src,
                "to_unit": dst,
                "value_out": round(converted, 8),
                "dimension": kind,
                "standard": "PE formula sheet (Conversion Formula)",
                "note": f"{value} {src} = {converted:.8g} {dst}",
                "formula_used": f"convert via SI base ({kind})",
            }
    return {
        "error": (
            f"Unsupported conversion {from_unit!r} → {to_unit!r}. "
            "Supported families: m↔ft, m2↔ft2, m3↔ft3, day↔hour, "
            "and pressure/stress (MPa, psi, kg/cm2, bar, N/mm2, ...)."
        ),
        "supported": sorted(
            set(_LENGTH_TO_M) | set(_AREA_TO_M2) | set(_VOLUME_TO_M3)
                | set(_TIME_TO_HOUR) | set(_PRESSURE_TO_KPA)
        ),
    }
