"""Quantity take-off calculators (additive library, drop-catalog gap-fill).

Geometry + material density — code-agnostic (no ACI/EC distinction). Rebar mass
uses the physical relation mass/m = (pi/4)*d^2 * density, which reproduces the
BS 8666 / standard bar-mass table (d=16 -> 1.578 kg/m). Rates/densities are
parameters; arithmetic shown in ``note``.
"""
from __future__ import annotations

import logging
import math
import os
import re

logger = logging.getLogger(__name__)

_STEEL_DENSITY = 7850.0  # kg/m^3

# Live UI pack E4 (Master Corpus / theshovel.ai):
#   "Concrete volume for a raft 30x20x1.5 m including your documented waste
#    factor." → net 900 m3; documented waste is 5% (× 1.05) → 945 m3.
# Leftover L6 aliased unnamed L×W×D to excavation_volume (bank only, no
# waste). A concrete/raft ask must pin concrete_volume and this factor.
# Kill switch: APPLY_DOCUMENTED_WASTE=0 restores the FAIL (net 900).
# Compose kill-switch: COMPOSE_CONCRETE_VOLUME=0 restores the leftover-E4
# hang (validate preamble ships, no 945). The calculator still returns 945;
# only the postprocess graft that writes the volume when the model stalls
# is disabled.
DOCUMENTED_CONCRETE_WASTE_FACTOR = 0.05

_CONCRETE_VOLUME_ASK_RE = re.compile(
    r"\b(concrete|raft|slab|footing|pile[\s-]?cap|waste[\s-]?factor)\b",
    re.IGNORECASE,
)
_DOCUMENTED_WASTE_ASK_RE = re.compile(
    r"\b(documented\s+waste|waste[\s-]?factor|including\b.{0,40}\bwaste)\b",
    re.IGNORECASE,
)
_LWT_CHAIN_RE = re.compile(
    r"(?<![A-Za-z0-9])(\d[\d,]*(?:\.\d+)?)\s*[x×*]\s*"
    r"(\d[\d,]*(?:\.\d+)?)\s*[x×*]\s*"
    r"(\d[\d,]*(?:\.\d+)?)"
    r"(?:\s*(?:mm|cm|m)\b)?",
    re.IGNORECASE,
)


def documented_waste_enabled() -> bool:
    """ON by default. ``APPLY_DOCUMENTED_WASTE=0/false/no/off`` is the kill-switch."""
    raw = (os.getenv("APPLY_DOCUMENTED_WASTE", "1") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def compose_concrete_volume_enabled() -> bool:
    """ON by default. ``COMPOSE_CONCRETE_VOLUME=0`` restores the validate hang."""
    raw = (os.getenv("COMPOSE_CONCRETE_VOLUME", "1") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _format_volume_figure(value: float) -> str:
    number = float(value)
    if number == int(number):
        return str(int(number))
    return f"{number:g}"


def answer_states_volume(text: str, volume: float) -> bool:
    """True when ``text`` already names the headline cubic-metre figure."""
    if not text:
        return False
    needle = _format_volume_figure(volume)
    compact = (text or "").replace(",", "")
    return needle in compact


def format_concrete_volume_line(inner: dict) -> str:
    """User-facing E4 line from a ``concrete_volume`` result dict."""
    if not isinstance(inner, dict):
        return ""
    volume = inner.get("volume_m3", inner.get("volume_with_waste_m3"))
    if volume is None:
        return ""
    net = inner.get("net_volume_m3")
    try:
        waste = float(inner.get("waste_factor") or 0.0)
    except (TypeError, ValueError):
        waste = 0.0
    vol_s = _format_volume_figure(volume)
    if waste > 0 and net is not None:
        pct = waste * 100.0
        factor = 1.0 + waste
        net_s = _format_volume_figure(net)
        return (
            f"Concrete volume including documented {pct:.0f}% waste: "
            f"{vol_s} m³ (net {net_s} × {factor:g})."
        )
    return f"Concrete volume: {vol_s} m³."


def compose_concrete_volume_from_ask(text: str) -> dict | None:
    """Run the documented-waste concrete calculator from the operator ask.

    Numbers live in the question (E4: 30×20×1.5). Leftover L6 earthwork is
    not a concrete/raft ask and returns None. Kill-switch
    ``COMPOSE_CONCRETE_VOLUME=0`` returns None (hang / no graft).
    """
    if not compose_concrete_volume_enabled():
        return None
    if not looks_like_concrete_volume_ask(text):
        return None
    if not parse_lwt_metres(text):
        return None
    from app.lib import construction_formulas as _cf
    result = _cf.run_calculation(None, {"text": text})
    if not isinstance(result, dict) or result.get("status") != "success":
        return None
    if result.get("calculation") != "concrete_volume":
        return None
    inner = result.get("result") if isinstance(result.get("result"), dict) else {}
    volume = inner.get("volume_m3")
    if volume is None:
        return None
    line = format_concrete_volume_line(inner)
    if not line:
        return None
    return {
        "volume_m3": volume,
        "net_volume_m3": inner.get("net_volume_m3"),
        "waste_factor": inner.get("waste_factor"),
        "note": inner.get("note") or "",
        "line": line,
        "envelope": result,
    }


def documented_concrete_waste_factor() -> float:
    """Project-documented concrete waste (5%), or 0.0 when the kill-switch is off."""
    if not documented_waste_enabled():
        return 0.0
    return DOCUMENTED_CONCRETE_WASTE_FACTOR


def looks_like_concrete_volume_ask(text: str) -> bool:
    """True when the ask is a concrete/raft/slab volume, not leftover-L6 earthwork."""
    return bool(_CONCRETE_VOLUME_ASK_RE.search(text or ""))


def parse_lwt_metres(text: str) -> tuple[float, float, float] | None:
    """First L×W×T (or L×W×D) chain in ``text``, in metres."""
    match = _LWT_CHAIN_RE.search(text or "")
    if not match:
        return None
    return tuple(float(g.replace(",", "")) for g in match.groups())  # type: ignore[return-value]


def resolve_concrete_volume_calc(
    calc: str | None,
    params: dict | None = None,
    text: str = "",
) -> tuple[str | None, dict]:
    """Pin ``concrete_volume`` + documented waste for a concrete/raft ask.

    Leftover L6 still owns unnamed trench L×W×D (no concrete/raft/waste words).
    E4's sheet phrasing is a concrete ask; excavation_volume would return the
    FAIL (bank 900, waste missing).
    """
    out = dict(params or {})
    blob = " ".join(
        str(x) for x in (
            text,
            calc,
            out.get("text"),
            out.get("formula"),
            out.get("name"),
            out.get("calculation"),
        ) if x
    )
    named = str(calc or "").strip()
    # E4 may steal unnamed / excavation_volume when the ask is a raft/slab.
    # A *named* calculator (dewatering, carbon, mix design, …) must stay on
    # its signature even if the question mentions "raft" or "concrete".
    # Live standing-exit: those names were remapped to concrete_volume and
    # returned volume_m3=0 (tool_error×2 / 1-of-2).
    _e4_stealable = {"", "excavation_volume", "concrete_volume"}
    is_concrete = named == "concrete_volume" or (
        named in _e4_stealable and looks_like_concrete_volume_ask(blob)
    )
    if not is_concrete:
        return calc, out

    dims = parse_lwt_metres(blob)
    if dims:
        out.setdefault("length_m", dims[0])
        out.setdefault("width_m", dims[1])
        out.setdefault("thickness_m", dims[2])

    if not out.get("thickness_m"):
        for key in ("depth_m", "height_m", "thickness", "depth", "height"):
            value = out.get(key)
            if value not in (None, "", 0, 0.0):
                out["thickness_m"] = value
                break

    if documented_waste_enabled():
        requested = out.get("waste_factor")
        if requested in (None, "", 0, 0.0) or _DOCUMENTED_WASTE_ASK_RE.search(blob):
            out["waste_factor"] = DOCUMENTED_CONCRETE_WASTE_FACTOR
    else:
        out["waste_factor"] = 0.0

    return "concrete_volume", out


# Phase 2 F–W (#43–84) routing. Agent C owns this slice; do not remap A–F
# names except to recover them from an E4 ``concrete_volume`` steal when
# the ask is clearly an F–W calculator (resource line / material
# consumption / an F–W registry name in the text).
FW_ROUTE_NAMES = frozenset({
    "fineness_modulus",
    "formwork_striking_time",
    "foundation_bearing_pressure",
    "grout_pressure_calc",
    "guardrail_top_rail_height",
    "interior_finishes_takeoff",
    "laser_scan_accuracy",
    "leed_points_estimate",
    "live_load_reduction",
    "masonry_wall_capacity",
    "material_consumption",
    "mobilization_cost_estimate",
    "modulus_of_elasticity_concrete",
    "modulus_of_rupture",
    "pe_unit_convert",
    "plumbing_flow_programme",
    "post_tensioning_force",
    "precast_beam_erection_check",
    "productivity_manpower_duration",
    "productivity_rate",
    "progress_quantity",
    "rc_beam_moment_capacity",
    "rc_beam_shear_capacity",
    "rebar_by_area",
    "rebar_lap_length",
    "rebar_weight",
    "resource_line_cost",
    "roi_calculator",
    "scaffold_load_capacity",
    "score_risk",
    "seismic_base_shear",
    "shear_stress_check",
    "slab_thickness_min",
    "slope_fos_simple",
    "steel_tension_capacity",
    "supervision_ratio",
    "thermal_shrinkage_equivalence",
    "unit_cost_total",
    "unit_weight_concrete",
    "weld_capacity",
    "wind_load_on_formwork",
    "wind_pressure",
})

_RESOURCE_LINE_TEXT_RE = re.compile(
    r"\b(resource\s+line|daily\s+output|day\s+rate|crew\s+days?)\b",
    re.IGNORECASE,
)
_CONSUMPTION_TEXT_RE = re.compile(
    r"\b(material\s+consumption|material\s+required|output\s+per\s+unit)\b",
    re.IGNORECASE,
)
_MIX_RATIO_RE = re.compile(r"\b\d+\s*:\s*\d+\s*:\s*\d+\b")
_MIX_TEXT_RE = re.compile(
    r"\b(mix\s+(?:design|proportion|ratio)|cement\s+parts|1\s*:\s*2\s*:\s*4)\b",
    re.IGNORECASE,
)
# Live Phase 2 leftover: "mobilization cost" / UK "mobilisation" / "site
# mob" do not contain the full registry string "mobilization cost estimate",
# so _blob_names_fw missed them and the LLM sometimes called the tool with
# no num_personnel / duration_months → TypeError → no SAR number.
_MOBILIZATION_TEXT_RE = re.compile(
    r"\b(mobili[sz]ation\s+cost|site\s+mobili[sz]ation|"
    r"mob(?:ilisation|ilization)?\s+cost|camp\s+mobili[sz]ation)\b",
    re.IGNORECASE,
)
_MOB_PERSONNEL_RE = re.compile(
    r"(\d[\d,]*)\s*(?:staff|personnel|people|workers|persons?|headcount|men)\b",
    re.IGNORECASE,
)
_MOB_MONTHS_RE = re.compile(
    r"(\d[\d,]*)\s*(?:months?|mos?\b)",
    re.IGNORECASE,
)
# Documented GCC example (audit oracle) — used only when the ask names
# mobilization but supplies no headcount / duration.
_MOB_DEFAULT_PERSONNEL = 100
_MOB_DEFAULT_MONTHS = 18
_PRESENT = (None, "", 0, 0.0)

# construction_calc presentation for the four live leftovers whose result
# had a number but no unit the phone UI / stream could copy. F–W (#43–84)
# only; A–F envelopes are untouched.
_FW_PRESENTATION = {
    "fineness_modulus": {"unit": "unitless", "value_keys": ("value",)},
    "score_risk": {"unit": "unitless", "value_keys": ("score",)},
    "slope_fos_simple": {"unit": "unitless", "value_keys": ("factor_of_safety",)},
    "mobilization_cost_estimate": {
        "unit": "SAR",
        "value_keys": ("grand_total_sar", "value"),
    },
}


def _fw_blob(calc: str | None, params: dict, text: str, original_name: str | None) -> str:
    return " ".join(
        str(x) for x in (
            text,
            calc,
            original_name,
            params.get("text"),
            params.get("formula"),
            params.get("name"),
            params.get("calculation"),
        ) if x
    )


def _param_present(params: dict, key: str) -> bool:
    return params.get(key) not in _PRESENT


def _blob_names_fw(blob: str) -> str | None:
    """Longest F–W registry name mentioned as snake, spaced, or 'of'-flexed."""
    if not blob:
        return None
    low = blob.lower()
    snake = re.sub(r"[\s\-]+", "_", low)
    hit: str | None = None
    for name in sorted(FW_ROUTE_NAMES, key=len, reverse=True):
        spaced = name.replace("_", " ")
        if name in snake or spaced in low:
            hit = name
            break
        parts = name.split("_")
        flex = r"\b" + r"\b(?:\s+of)?\s+".join(re.escape(p) for p in parts) + r"\b"
        if re.search(flex, low):
            hit = name
            break
    return hit


def _looks_like_resource_line(params: dict, blob: str) -> bool:
    if _param_present(params, "daily_output") or _param_present(params, "day_rate"):
        return True
    return bool(_RESOURCE_LINE_TEXT_RE.search(blob or ""))


def _looks_like_mix(params: dict, blob: str) -> bool:
    if _param_present(params, "cement_parts"):
        return True
    return bool(_MIX_RATIO_RE.search(blob or "") or _MIX_TEXT_RE.search(blob or ""))


def _looks_like_consumption(params: dict, blob: str) -> bool:
    if _param_present(params, "quantity_of_work") and _param_present(params, "output_per_unit"):
        return True
    if _param_present(params, "output_per_unit") and (
        _param_present(params, "quantity") or _param_present(params, "quantity_of_work")
    ):
        return True
    if _CONSUMPTION_TEXT_RE.search(blob or "") and not _looks_like_mix(params, blob):
        return True
    return False


def _alias_resource_line_params(out: dict) -> None:
    if "day_rate" not in out and out.get("unit_rate") not in (None, ""):
        out["day_rate"] = out["unit_rate"]


def _alias_consumption_params(out: dict) -> None:
    if "quantity_of_work" not in out or out.get("quantity_of_work") in (None, ""):
        if out.get("quantity") not in (None, ""):
            out["quantity_of_work"] = out["quantity"]
    # E4 injects waste_factor=0.05 (a fraction). material_consumption treats
    # waste_factor as the full multiplier (1.05). Convert fractions.
    factor = out.get("waste_factor")
    try:
        factor_f = float(factor) if factor not in (None, "") else None
    except (TypeError, ValueError):
        factor_f = None
    if factor_f is not None and 0 < factor_f < 1:
        if out.get("waste_percent") in (None, ""):
            out["waste_percent"] = factor_f * 100.0
        out.pop("waste_factor", None)


def _looks_like_mobilization(_params: dict, blob: str) -> bool:
    return bool(_MOBILIZATION_TEXT_RE.search(blob or ""))


def _int_from_match(match: re.Match[str] | None) -> int | None:
    if match is None:
        return None
    raw = (match.group(1) or "").replace(",", "")
    if not raw.isdigit():
        return None
    return int(raw)


def _alias_mobilization_params(out: dict, blob: str = "") -> None:
    """Fill headcount / duration so construction_calc always returns a SAR figure."""
    search = " ".join(
        str(x) for x in (
            blob, out.get("text"), out.get("formula"), out.get("query"),
        ) if x
    )
    if not _param_present(out, "num_personnel"):
        parsed = _int_from_match(_MOB_PERSONNEL_RE.search(search))
        out["num_personnel"] = parsed if parsed is not None else _MOB_DEFAULT_PERSONNEL
    if not _param_present(out, "duration_months"):
        parsed = _int_from_match(_MOB_MONTHS_RE.search(search))
        out["duration_months"] = parsed if parsed is not None else _MOB_DEFAULT_MONTHS


def _fw_presentation_note(unit: str, value: object, existing: object) -> str:
    if isinstance(existing, list):
        existing = " ".join(str(x) for x in existing)
    note = str(existing or "").strip()
    if value is None:
        token = unit
    elif isinstance(value, float) and value == int(value):
        token = f"{int(value)} {unit}"
    elif isinstance(value, float):
        token = f"{value:g} {unit}"
    else:
        token = f"{value} {unit}"
    if unit.lower() in note.lower() and (
        value is None or str(value) in note.replace(",", "")
    ):
        return note
    if note:
        return f"{note} Result {token}."
    return f"Result {token}."


def shape_fw_calc_result(name: str, result: dict) -> dict:
    """Stamp ``unit`` (and a copyable note) on the four live leftover results.

    Fineness modulus / risk score / infinite-slope FoS are dimensionless —
    the live probe needs the word ``unitless`` in the tool JSON. Mobilization
    already has ``*_sar`` keys; the stream still needs a headline ``value``
    + ``unit: SAR``.
    """
    spec = _FW_PRESENTATION.get(name)
    if not spec or not isinstance(result, dict):
        return result
    if isinstance(result.get("error"), str):
        return result
    out = dict(result)
    unit = spec["unit"]
    value = None
    for key in spec["value_keys"]:
        if out.get(key) not in (None, ""):
            value = out[key]
            break
    out.setdefault("unit", unit)
    if value is not None:
        out.setdefault("value", value)
    out["note"] = _fw_presentation_note(
        unit, value, out.get("note") or out.get("description"),
    )
    return out


def resolve_fw_calc(
    calc: str | None,
    params: dict | None = None,
    text: str = "",
    original_name: str | None = None,
) -> tuple[str | None, dict]:
    """Pin F–W calculators the LLM (or E4) mis-named.

    Live Phase 2 modes this owns:
    - ``resource_line_cost`` asked, model picked ``unit_cost_total``
    - ``material_consumption`` asked, model (or E4 ``concrete`` pin) picked
      ``concrete_mix_proportions`` / ``concrete_volume``
    - text names an F–W registry function (snake or spaced)
    - mobilization / mobilisation / site-mob language, including a no-figure
      ask (fill documented 100 staff × 18 months so a SAR number always lands)

    True qty×rate and true 1:2:4 mix are left alone. A raft L×W×T volume
    ask stays on ``concrete_volume``.
    """
    out = dict(params or {})
    blob = _fw_blob(calc, out, text, original_name)
    named = str(calc or "").strip() or None
    original = str(original_name or "").strip() or None
    pinned = _blob_names_fw(blob)

    stealable = {
        None, "", "unit_cost_total", "concrete_mix_proportions",
        "concrete_volume", "excavation_volume",
    }

    if _looks_like_resource_line(out, blob) and not (
        pinned == "unit_cost_total" and not (
            _param_present(out, "daily_output") or _param_present(out, "day_rate")
        )
    ):
        if named in stealable or pinned == "resource_line_cost":
            _alias_resource_line_params(out)
            return "resource_line_cost", out

    if _looks_like_consumption(out, blob) and not _looks_like_mix(out, blob):
        if named in stealable or pinned == "material_consumption":
            _alias_consumption_params(out)
            return "material_consumption", out

    if _looks_like_mobilization(out, blob) and (
        named in stealable or pinned == "mobilization_cost_estimate"
        or named == "mobilization_cost_estimate"
    ):
        _alias_mobilization_params(out, blob)
        return "mobilization_cost_estimate", out

    if named == "concrete_volume" and original in FW_ROUTE_NAMES:
        if original == "mobilization_cost_estimate":
            _alias_mobilization_params(out, blob)
        return original, out

    if pinned and named in stealable | {None}:
        if pinned == "resource_line_cost":
            _alias_resource_line_params(out)
        elif pinned == "material_consumption":
            _alias_consumption_params(out)
        elif pinned == "mobilization_cost_estimate":
            _alias_mobilization_params(out, blob)
        return pinned, out

    if pinned and named == "concrete_volume" and pinned != "unit_cost_total":
        if pinned == "material_consumption":
            _alias_consumption_params(out)
        elif pinned == "mobilization_cost_estimate":
            _alias_mobilization_params(out, blob)
        return pinned, out

    if named == "mobilization_cost_estimate":
        _alias_mobilization_params(out, blob)
        return named, out

    return calc, out


def concrete_volume(
    length_m: float = 0.0,
    width_m: float = 0.0,
    thickness_m: float = 0.0,
    shape: str = "rectangular",
    diameter_m: float = 0.0,
    height_m: float = 0.0,
    top_width_m: float = 0.0,
    bottom_width_m: float = 0.0,
    depth_m: float = 0.0,
    waste_factor: float = 0.05,
) -> dict:
    """Concrete volume for a rectangular slab/element, a cylinder (column/pile),
    or a trapezoidal section (channel/footing), plus a waste allowance.

    rectangular: L*W*T. cylinder: pi*(D/2)^2*H.
    trapezoidal: ((top+bottom)/2 * depth) * length.

    Headline ``volume_m3`` is the with-waste figure (E4 expects 945, not net
    900). ``APPLY_DOCUMENTED_WASTE=0`` zeros the factor and restores net.
    """
    if min(float(length_m), float(width_m), float(thickness_m),
           float(diameter_m), float(height_m), float(top_width_m),
           float(bottom_width_m), float(depth_m)) < 0:
        return {"error": "concrete_volume dimensions must be >= 0."}
    if not documented_waste_enabled():
        waste_factor = 0.0
    else:
        waste_factor = float(waste_factor)
    if waste_factor < 0:
        return {"error": "waste_factor must be >= 0."}
    s = (shape or "rectangular").strip().lower()
    if s == "cylinder":
        net = math.pi * (diameter_m / 2.0) ** 2 * height_m
        expr = f"pi*({diameter_m}/2)^2*{height_m}"
    elif s == "trapezoidal":
        area = (top_width_m + bottom_width_m) / 2.0 * depth_m
        net = area * length_m
        expr = f"(({top_width_m}+{bottom_width_m})/2*{depth_m})*{length_m}"
    else:
        s = "rectangular"
        net = length_m * width_m * thickness_m
        expr = f"{length_m}*{width_m}*{thickness_m}"
    with_waste = net * (1.0 + waste_factor)
    headline = round(with_waste, 3)
    net_r = round(net, 3)
    return {
        "shape": s,
        "volume_m3": headline,
        "net_volume_m3": net_r,
        "volume_with_waste_m3": headline,
        "value": headline,
        "waste_factor": waste_factor,
        "standard": "geometry",
        "note": (f"Net = {expr} = {net:.3f} m3; "
                 f"+{waste_factor*100:.0f}% waste = {with_waste:.3f} m3."),
    }


_WEIGHT_TO_LENGTH_MODES = {
    "weight_to_length",
    "weight2length",
    "to_length",
    "metres_run",
    "meters_run",
    "metre_run",
    "meter_run",
}


def _rebar_mass_kg(
    total_weight_kg: float,
    total_mass_kg: float,
    total_mass_t: float,
) -> float:
    for raw, scale in (
        (total_weight_kg, 1.0),
        (total_mass_kg, 1.0),
        (total_mass_t, 1000.0),
    ):
        if raw in (None, "", 0, 0.0):
            continue
        try:
            value = float(raw) * scale
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0.0


def rebar_weight(
    bar_diameter_mm: float,
    total_length_m: float = 0.0,
    quantity: int = 1,
    density_kg_m3: float = _STEEL_DENSITY,
    total_weight_kg: float = 0.0,
    total_mass_kg: float = 0.0,
    total_mass_t: float = 0.0,
    mode: str = "",
) -> dict:
    """Mass of reinforcement bars, or metres run from a given mass.

    Unit mass = (pi/4)*d^2 * density (kg/m), reproducing the standard
    bar-mass table (d=16 -> 1.578 kg/m at 7850 kg/m3).

    Live A2-2: ``mode=weight_to_length`` + ``total_weight_kg`` (12 t of
    Y16) returns metres run. ``total_length_m`` stays required for the
    original length → mass path.
    """
    d = float(bar_diameter_mm)
    area_m2 = math.pi / 4.0 * (d / 1000.0) ** 2
    unit_mass = area_m2 * density_kg_m3  # kg/m
    qty = int(quantity) if quantity not in (None, "") else 1
    if qty <= 0:
        qty = 1
    try:
        length = float(total_length_m or 0.0)
    except (TypeError, ValueError):
        length = 0.0
    mass_kg = _rebar_mass_kg(total_weight_kg, total_mass_kg, total_mass_t)
    want_length = str(mode or "").strip().lower() in _WEIGHT_TO_LENGTH_MODES
    if not want_length and mass_kg > 0 and length <= 0:
        want_length = True

    if want_length:
        if unit_mass <= 0:
            return {
                "error": (
                    "rebar_weight weight_to_length needs bar_diameter_mm > 0 "
                    "to compute unit mass."
                ),
            }
        if mass_kg <= 0:
            return {
                "error": (
                    "rebar_weight needs bar_diameter_mm (mm) and either "
                    "total_length_m (m) or total_weight_kg (kg) with "
                    "mode=weight_to_length."
                ),
                "required": ["bar_diameter_mm", "total_weight_kg"],
            }
        metres = mass_kg / unit_mass / qty
        return {
            "unit_mass_kg_m": round(unit_mass, 4),
            "total_mass_kg": round(mass_kg, 2),
            "total_mass_t": round(mass_kg / 1000.0, 4),
            "total_length_m": round(metres, 2),
            "metres_run": round(metres, 2),
            "quantity": qty,
            "mode": "weight_to_length",
            "standard": "BS 8666 / bar-mass relation",
            "note": (
                f"Unit mass = (pi/4)*({d}/1000)^2*{density_kg_m3:.0f} = "
                f"{unit_mass:.4f} kg/m; {mass_kg:g} kg / {unit_mass:.4f} "
                f"kg/m = {metres:.2f} m."
            ),
        }

    if length <= 0:
        return {
            "error": (
                "rebar_weight needs bar_diameter_mm (mm) and either "
                "total_length_m (m) or total_weight_kg (kg) with "
                "mode=weight_to_length."
            ),
            "required": ["bar_diameter_mm", "total_length_m"],
        }

    total = unit_mass * length * qty
    return {
        "unit_mass_kg_m": round(unit_mass, 4),
        "total_mass_kg": round(total, 2),
        "total_mass_t": round(total / 1000.0, 4),
        "total_length_m": round(length, 4),
        "quantity": qty,
        "standard": "BS 8666 / bar-mass relation",
        "note": (f"Unit mass = (pi/4)*({d}/1000)^2*{density_kg_m3:.0f} = "
                 f"{unit_mass:.4f} kg/m; x {length} m x {qty} = "
                 f"{total:.2f} kg."),
    }


def rebar_by_area(
    area_m2: float,
    spacing_mm: float,
    bar_diameter_mm: float,
    both_ways: bool = False,
    density_kg_m3: float = _STEEL_DENSITY,
) -> dict:
    """Reinforcement mass for a slab/wall area from a bar spacing. Bars per metre
    = 1000/spacing; length per m2 ~= bars/m (one way) x area; both_ways doubles."""
    d = float(bar_diameter_mm)
    unit_mass = math.pi / 4.0 * (d / 1000.0) ** 2 * density_kg_m3  # kg/m
    bars_per_m = 1000.0 / float(spacing_mm)
    length_per_m2 = bars_per_m  # 1 m run of bar per bar-line across a 1 m width
    ways = 2 if both_ways else 1
    total_length = length_per_m2 * float(area_m2) * ways
    total_mass = total_length * unit_mass
    return {
        "bars_per_m": round(bars_per_m, 3),
        "total_bar_length_m": round(total_length, 2),
        "total_mass_kg": round(total_mass, 2),
        "both_ways": both_ways,
        "standard": "geometry / bar-mass relation",
        "note": (f"{bars_per_m:.2f} bars/m x {area_m2} m2 x {ways} way(s) = "
                 f"{total_length:.1f} m; x {unit_mass:.4f} kg/m = {total_mass:.1f} kg."),
    }




def interior_finishes_takeoff(
    floor_area_m2: float,
    perimeter_m: float,
    room_count: int,
    floor_to_ceiling_m: float,
    door_width_m: float = 0.0,
    door_height_m: float = 0.0,
    doors_per_room: float = 0.0,
    shared_wall_fraction: float = 0.5,
    window_deduction_m2: float = 0.0,
) -> dict:
    """Interior finishes quantities from a room take-off -- PROCESS, not facts.

    Every project-specific value is a PARAMETER: the floor-to-ceiling height,
    door schedule, and window deductions change per project and per floor, so
    none of them has a baked-in value here. ``floor_to_ceiling_m`` is required
    -- calling without it is a refusal, not a guess. ``shared_wall_fraction``
    models internal walls being shared between two rooms (each wall face is
    counted once per room in the summed perimeters, so the wall ELEMENT area
    is roughly half the summed face area); it is an explicit modelling
    parameter, stated in the basis, adjustable per project.

    Outputs: screed/tiling/ceiling areas (= floor area), skirting length
    (perimeter minus door widths), wall paint area (perimeter x height minus
    door and window deductions), and blockwork element area (shared-wall
    fraction of the net wall face area). The ``basis`` echoes every input so
    the take-off is auditable line by line.
    """
    if floor_to_ceiling_m <= 0:
        return {
            "status": "error",
            "error": "floor_to_ceiling_m is required (> 0): the height varies "
                     "per project and per floor and is never assumed here. "
                     "Take it from the section drawing or project facts.",
        }
    fa = float(floor_area_m2)
    pm = float(perimeter_m)
    rooms = int(room_count)
    h = float(floor_to_ceiling_m)
    doors_total = rooms * float(doors_per_room)
    door_area = float(door_width_m) * float(door_height_m)
    door_area_total = doors_total * door_area
    door_width_total = doors_total * float(door_width_m)

    gross_wall_face = pm * h
    net_wall_paint = max(gross_wall_face - door_area_total - float(window_deduction_m2), 0.0)
    blockwork = net_wall_paint * float(shared_wall_fraction)
    skirting = max(pm - door_width_total, 0.0)

    return {
        "floor_screed_m2": round(fa, 2),
        "floor_tiling_m2": round(fa, 2),
        "ceiling_finish_m2": round(fa, 2),
        "skirting_m": round(skirting, 2),
        "wall_paint_m2": round(net_wall_paint, 2),
        "blockwork_m2": round(blockwork, 2),
        "gross_wall_face_m2": round(gross_wall_face, 2),
        "door_deduction_m2": round(door_area_total, 2),
        "standard": "geometry (parameterised process)",
        "basis": [
            f"floor area {fa} m2; perimeter {pm} m; rooms {rooms}",
            f"floor-to-ceiling {h} m (project input, not a default)",
            f"doors: {doors_per_room}/room x {rooms} rooms, "
            f"{door_width_m} x {door_height_m} m = {door_area_total:.2f} m2 deducted",
            f"window deduction {window_deduction_m2} m2 (project input)",
            f"shared wall fraction {shared_wall_fraction} (modelling parameter)",
            "wall paint = perimeter x height - doors - windows; "
            "blockwork = wall face x shared fraction; "
            "skirting = perimeter - door widths",
        ],
    }


def resource_line_cost(
    quantity: float,
    daily_output: float,
    day_rate: float,
    material_rate_per_unit: float = -1.0,
    plant_fraction_of_labour: float = 0.0,
) -> dict:
    """Material / labour / plant split for one estimate line -- PROCESS only.

    The productivity (daily output per tradesman) and the day rate vary per
    market, project, and even floor: both are required inputs, never
    defaults. Labour = quantity / daily_output x day_rate. Material rate is
    optional; when absent (< 0) the material cell is reported as
    NO SOURCED RATE rather than a number, mirroring the estimating rule that
    a missing rate is a refusal, not an invention. Plant is modelled as a
    declared fraction of labour (small plant and scaffold allowance).
    """
    if daily_output <= 0 or day_rate <= 0:
        return {
            "status": "error",
            "error": "daily_output and day_rate are required (> 0): "
                     "productivity and rates vary per project and are never "
                     "assumed here. Supply them from project facts, the "
                     "knowledge base, or the operator.",
        }
    q = float(quantity)
    crew_days = q / float(daily_output)
    labour = crew_days * float(day_rate)
    plant = labour * float(plant_fraction_of_labour)
    has_material = float(material_rate_per_unit) >= 0
    material = q * float(material_rate_per_unit) if has_material else None
    total = labour + plant + (material or 0.0)
    return {
        "quantity": q,
        "crew_days": round(crew_days, 2),
        "labour_cost": round(labour, 2),
        "plant_cost": round(plant, 2),
        "material_cost": round(material, 2) if has_material else "NO SOURCED RATE",
        "total_cost": round(total, 2),
        "total_is_partial": not has_material,
        "standard": "resource build-up (parameterised process)",
        "note": (f"labour = {q}/{daily_output} x {day_rate} = {labour:.2f}; "
                 f"plant = {plant_fraction_of_labour} x labour = {plant:.2f}; "
                 + (f"material = {q} x {material_rate_per_unit} = {material:.2f}."
                    if has_material else "material: NO SOURCED RATE (no rate supplied).")),
    }


# Live A2-2: "What is the weight of 12 tonnes of Y16 bars in metres run?"
_METRES_RUN_ASK_RE = re.compile(
    r"(?i)metres?\s+run|meters?\s+run|"
    r"(?:tonnes?|tons?|kg).{0,40}(?:y|t|h)?\d{1,2}.{0,40}(?:metr|length|run)|"
    r"(?:y|t|h)\d{1,2}.{0,40}(?:tonnes?|tons?|kg).{0,40}(?:metr|length|run)"
)
_Y_BAR_RE = re.compile(r"(?i)\b[YTH](\d{1,2})\b")
_DIA_MM_RE = re.compile(r"(?i)(\d{1,2})\s*mm\b")
_TONNES_RE = re.compile(r"(?i)(\d[\d,]*(?:\.\d+)?)\s*(?:tonnes?|tons?|t)\b")
_KG_MASS_RE = re.compile(r"(?i)(\d[\d,]*(?:\.\d+)?)\s*kg\b")


def looks_like_rebar_metres_run_ask(text: str) -> bool:
    """True when the operator asked for metres run from a bar mass."""
    return bool(_METRES_RUN_ASK_RE.search(text or ""))


def parse_rebar_metres_run_ask(text: str) -> tuple[float, float] | None:
    """Return (bar_diameter_mm, total_weight_kg) or None."""
    raw = text or ""
    dia = None
    y = _Y_BAR_RE.search(raw)
    if y:
        dia = float(y.group(1))
    else:
        d = _DIA_MM_RE.search(raw)
        if d:
            dia = float(d.group(1))
    if dia is None or dia <= 0:
        return None
    tonnes = _TONNES_RE.search(raw)
    if tonnes:
        return dia, float(tonnes.group(1).replace(",", "")) * 1000.0
    kg = _KG_MASS_RE.search(raw)
    if kg:
        return dia, float(kg.group(1).replace(",", ""))
    return None


def format_rebar_metres_run_line(inner: dict) -> str:
    """User-facing metres-run line. Rejects the 1 m unit-mass demo."""
    if not isinstance(inner, dict):
        return ""
    metres = inner.get("metres_run", inner.get("total_length_m"))
    unit = inner.get("unit_mass_kg_m")
    mass = inner.get("total_mass_kg")
    try:
        metres_f = float(metres)
    except (TypeError, ValueError):
        logger.debug("metres_run is not numeric: %r", metres)
        return ""
    if metres_f <= 10:
        return ""
    bits = [f"{metres_f:,.2f} m"]
    if unit not in (None, "") and mass not in (None, ""):
        bits = [
            f"Unit mass = {float(unit):.4f} kg/m; "
            f"{float(mass):,.0f} kg / {float(unit):.4f} = {metres_f:,.2f} m"
        ]
    return bits[0] + "."


def compose_rebar_metres_run_from_ask(text: str) -> dict | None:
    """Run weight→length from the operator ask (live A2-2)."""
    if not looks_like_rebar_metres_run_ask(text):
        return None
    parsed = parse_rebar_metres_run_ask(text)
    if not parsed:
        return None
    dia, mass_kg = parsed
    from app.lib import construction_formulas as _cf
    env = _cf.run_calculation(
        "rebar_weight",
        {
            "bar_diameter_mm": dia,
            "total_weight_kg": mass_kg,
            "mode": "weight_to_length",
        },
    )
    if not isinstance(env, dict) or env.get("status") != "success":
        return None
    inner = env.get("result") if isinstance(env.get("result"), dict) else {}
    line = format_rebar_metres_run_line(inner)
    if not line:
        return None
    metres = inner.get("metres_run", inner.get("total_length_m"))
    return {
        "metres_run": metres,
        "total_length_m": metres,
        "unit_mass_kg_m": inner.get("unit_mass_kg_m"),
        "total_mass_kg": inner.get("total_mass_kg"),
        "line": line,
        "envelope": env,
        "result": inner,
    }


ADDITIONAL_CALCULATORS = {
    "concrete_volume": concrete_volume,
    "rebar_weight": rebar_weight,
    "rebar_by_area": rebar_by_area,
    "interior_finishes_takeoff": interior_finishes_takeoff,
    "resource_line_cost": resource_line_cost,
}
