"""validation_pipeline — runnable 5-stage validation checker.

The heavy-reasoning agent prompt described a 5-stage validation pipeline
(syntactic / dimensional / physical / empirical / operational) but only as
prose for the LLM to "do in its head." When the LLM made a unit-conversion
error in a NumPy code-gen task (5,900 °C instead of 5.9 °C ΔT — multiplied
*and* divided by 1000), nothing caught it because the validation was
unimplemented.

This block makes those checks runnable. The agent can call it after any
sympy_reasoning, formula_executor, or numeric-bearing tool result, get a
deterministic pass/fail per stage, and refuse to report a number that
failed validation.

Input shape::

    {
        "value": 5.9,                          # required, must be numeric
        "unit": "degC",                        # optional, Pint string
        "context": {
            "material_type": "concrete",       # picks the empirical range
            "metric": "rate_usd_per_m3",       # picks the empirical range
            "physical_min": 0,                 # optional override
            "physical_max": 1e9,
            "empirical_min": 100,
            "empirical_max": 250,
            # Operational stage uses paired fields:
            "duration_weeks": 16,
            "available_weeks": 8,
        }
    }

Output::

    {
        "status": "success",
        "overall": "pass" | "fail",
        "stages": {
            "syntactic":   {"pass": bool, "reason": str},
            "dimensional": {"pass": bool, "reason": str},
            "physical":    {"pass": bool, "reason": str},
            "empirical":   {"pass": bool, "reason": str},
            "operational": {"pass": bool, "reason": str},
        },
        "first_failure": Optional[str],
    }
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

from app.core.universal_base import UniversalBlock


# Industry-rough sanity ranges. Used as the EMPIRICAL backstop when the
# caller doesn't supply explicit empirical_min/empirical_max. Numbers are
# global rough averages, NOT precise — they exist to catch order-of-
# magnitude errors (5,900 °C vs 5.9 °C, 1500 USD/m³ vs 150 USD/m³, etc.).
EMPIRICAL_RANGES: Dict[Tuple[str, str], Tuple[float, float]] = {
    # (material_type, metric) -> (min, max)
    ("concrete",  "rate_usd_per_m3"):     (50.0,   500.0),
    ("concrete",  "volume_m3"):           (1.0,    5_000_000.0),
    ("concrete",  "compressive_mpa"):     (10.0,   80.0),
    ("steel",     "rate_usd_per_kg"):     (0.5,    10.0),
    ("steel",     "weight_kg"):           (1.0,    100_000_000.0),
    ("rebar",     "rate_usd_per_kg"):     (0.5,    8.0),
    ("rebar",     "length_m"):            (1.0,    50_000_000.0),
    ("excavation","volume_m3"):           (1.0,    50_000_000.0),
    ("formwork",  "rate_usd_per_m2"):     (10.0,   200.0),
    ("formwork",  "area_m2"):             (1.0,    5_000_000.0),
    # Generic temperature / time / cost ranges (used when caller passes
    # one of these as the metric without a material_type).
    ("__any__",   "temperature_degc"):    (-40.0,  500.0),  # heat in HVAC + curing
    ("__any__",   "duration_weeks"):      (0.0,    1040.0),  # 20 years cap
    ("__any__",   "cost_usd"):            (0.0,    1e10),
    ("__any__",   "percent"):             (0.0,    100.0),
}


def _check_syntactic(value: Any) -> Dict[str, Any]:
    if value is None:
        return {"pass": False, "reason": "value is None"}
    if isinstance(value, bool):
        return {"pass": False, "reason": "value is a bool, not a numeric"}
    if not isinstance(value, (int, float)):
        try:
            float(value)
        except Exception:
            return {"pass": False, "reason": f"value is not numeric (got {type(value).__name__})"}
        return {"pass": True, "reason": "value is string-numeric; coerce before reporting"}
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return {"pass": False, "reason": f"value is non-finite ({value})"}
    return {"pass": True, "reason": "value is finite numeric"}


def _check_dimensional(value: Any, unit: Optional[str]) -> Dict[str, Any]:
    """Run a Pint sanity check on the unit string, with two carve-outs:

    * Currency-bearing units (USD/m3, SAR/kg, etc.) — Pint doesn't model
      currencies by default, so we strip the currency token and re-check
      the remaining physical units instead of marking the whole result
      as failed.
    * Offset-unit ambiguity (degC, degF used as a delta) — Pint refuses
      to multiply scalars by an offset-bearing unit; we re-try with the
      `delta_` prefix which is Pint's syntax for "this is a difference".
    """
    if not unit:
        return {"pass": True, "reason": "no unit declared — dimensional check skipped"}
    try:
        import pint
    except ImportError:
        return {"pass": True, "reason": "pint not installed — dimensional check skipped"}

    import re
    ureg = pint.UnitRegistry()

    # Carve-out 1: strip currency tokens before checking.
    currency_re = re.compile(r"\b(USD|SAR|AED|EUR|GBP|JPY|CNY|AUD|CAD|KWD|QAR|BHD|OMR|INR|PKR)\b", re.IGNORECASE)
    stripped = currency_re.sub("", unit)
    stripped = re.sub(r"^[/\s*·]+|[/\s*·]+$", "", stripped).strip("/ ")
    if stripped != unit and not stripped:
        return {"pass": True, "reason": f"unit '{unit}' is currency-only — outside dimensional check scope"}
    probe = stripped or unit

    # Carve-out 3: construction shorthand. Pint wants `m**3`, not `m3` —
    # site engineers always write the latter. Expand `<unit><digit>` to
    # the explicit power form before handing to Pint.
    probe = re.sub(r"\b(m|mm|cm|km|in|ft|yd)([2-4])\b", r"\1**\2", probe)

    # Carve-out 2: offset-unit retry with delta_ prefix.
    def _try(u: str):
        try:
            q = float(value) * ureg(u)
            return True, str(q.units)
        except Exception as e:
            return False, f"{type(e).__name__}: {str(e)[:120]}"

    ok, info = _try(probe)
    if ok:
        return {"pass": True, "reason": f"unit '{unit}' parsed as {info}"}
    if "Offset" in info or "OffsetUnitCalculusError" in info:
        # Pint treats degC as an absolute temperature; deltas need `delta_degC`.
        delta_probe = re.sub(r"\b(deg[CF]|celsius|fahrenheit|degree[CF])\b",
                             lambda m: f"delta_{m.group(1)}",
                             probe, flags=re.IGNORECASE)
        ok2, info2 = _try(delta_probe)
        if ok2:
            return {"pass": True, "reason": f"unit '{unit}' parsed as delta-{info2}"}
    return {"pass": False, "reason": f"unit '{unit}' not recognised by pint ({info})"}


def _check_physical(value: float, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Hard physical bounds — usually positivity + a generous ceiling."""
    pmin = ctx.get("physical_min")
    pmax = ctx.get("physical_max")
    # Default: most construction metrics are non-negative.
    if pmin is None:
        pmin = -1e30 if ctx.get("metric") == "temperature_degc" else 0.0
    if pmax is None:
        pmax = 1e30
    try:
        v = float(value)
    except Exception:
        return {"pass": False, "reason": "value not coercible to float for physical check"}
    if v < pmin:
        return {"pass": False, "reason": f"value {v} below physical_min {pmin}"}
    if v > pmax:
        return {"pass": False, "reason": f"value {v} above physical_max {pmax}"}
    return {"pass": True, "reason": f"value {v} within physical bounds [{pmin}, {pmax}]"}


def _check_empirical(value: float, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Rough industry sanity range. Order-of-magnitude check, not precision."""
    emin = ctx.get("empirical_min")
    emax = ctx.get("empirical_max")
    if emin is None or emax is None:
        material = (ctx.get("material_type") or "").lower().strip()
        metric = (ctx.get("metric") or "").lower().strip()
        rng = (EMPIRICAL_RANGES.get((material, metric))
               or EMPIRICAL_RANGES.get(("__any__", metric)))
        if rng:
            emin = emin if emin is not None else rng[0]
            emax = emax if emax is not None else rng[1]
        else:
            return {"pass": True, "reason": "no empirical range for (material, metric) — skipped"}
    try:
        v = float(value)
    except Exception:
        return {"pass": False, "reason": "value not coercible to float for empirical check"}
    # The empirical stage is "5× off" — beyond 5x outside the range, flag.
    slack_lo = emin / 5.0 if emin > 0 else emin
    slack_hi = emax * 5.0
    if v < slack_lo:
        return {"pass": False, "reason": f"value {v} far below empirical range [{emin}, {emax}] (5x slack {slack_lo})"}
    if v > slack_hi:
        return {"pass": False, "reason": f"value {v} far above empirical range [{emin}, {emax}] (5x slack {slack_hi})"}
    if v < emin or v > emax:
        return {"pass": True, "reason": f"value {v} outside tight empirical range [{emin}, {emax}] but within 5x slack — borderline"}
    return {"pass": True, "reason": f"value {v} within empirical range [{emin}, {emax}]"}


def _check_operational(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Compare paired fields when present (e.g. procurement vs. site need)."""
    duration = ctx.get("duration_weeks")
    available = ctx.get("available_weeks")
    if duration is None or available is None:
        return {"pass": True, "reason": "no operational fields supplied — skipped"}
    try:
        d, a = float(duration), float(available)
    except Exception:
        return {"pass": False, "reason": "operational fields not numeric"}
    if d <= a:
        return {"pass": True, "reason": f"duration {d}w fits available {a}w"}
    return {
        "pass": False,
        "reason": f"action not achievable: duration {d}w exceeds available {a}w (need {d - a}w more)",
    }


class ValidationPipelineBlock(UniversalBlock):
    """Runs the 5-stage validation pipeline the heavy-reasoning prompt
    documents but the LLM was previously expected to perform unaided.
    """

    name = "validation_pipeline"
    version = "1.0.0"
    description = (
        "Runnable 5-stage validation (syntactic / dimensional / physical / empirical / "
        "operational) for numeric results. Returns per-stage pass/fail plus an overall "
        "verdict so the agent can refuse to report numbers that failed validation."
    )
    layer = 3
    tags = ["domain", "construction", "validation", "reasoning"]
    requires = []

    ui_schema = {
        "input": {
            "type": "json",
            "placeholder": (
                '{"value": 5.9, "unit": "degC", '
                '"context": {"material_type": "concrete", "metric": "temperature_degc"}}'
            ),
            "multiline": True,
        },
        "output": {
            "type": "json",
            "fields": [
                {"name": "overall", "type": "text", "label": "Verdict"},
                {"name": "stages", "type": "json", "label": "Per-stage results"},
                {"name": "first_failure", "type": "text", "label": "First failed stage"},
            ],
        },
        "quick_actions": [
            {"icon": "✅", "label": "Validate", "prompt": "Run the 5-stage validation pipeline"},
        ],
    }

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        data = input_data if isinstance(input_data, dict) else {}
        if not data and not params:
            return {"status": "error", "error": "Provide {value, unit?, context?} as input"}

        value = data.get("value", params.get("value"))
        unit = data.get("unit", params.get("unit"))
        ctx = dict(data.get("context") or params.get("context") or {})

        # Stage 1 — syntactic gates the rest. If the input isn't numeric,
        # downstream checks can't run cleanly.
        syntactic = _check_syntactic(value)
        if not syntactic["pass"]:
            return {
                "status": "success",
                "overall": "fail",
                "stages": {
                    "syntactic": syntactic,
                    "dimensional": {"pass": False, "reason": "skipped — syntactic failed"},
                    "physical":    {"pass": False, "reason": "skipped — syntactic failed"},
                    "empirical":   {"pass": False, "reason": "skipped — syntactic failed"},
                    "operational": {"pass": False, "reason": "skipped — syntactic failed"},
                },
                "first_failure": "syntactic",
            }

        # Coerce stringy-numeric to float before continuing.
        v = float(value) if not isinstance(value, (int, float)) else value

        dimensional = _check_dimensional(v, unit)
        physical = _check_physical(v, ctx)
        empirical = _check_empirical(v, ctx)
        operational = _check_operational(ctx)

        stages = {
            "syntactic": syntactic,
            "dimensional": dimensional,
            "physical": physical,
            "empirical": empirical,
            "operational": operational,
        }
        first_failure = next(
            (name for name, r in stages.items() if not r.get("pass")),
            None,
        )
        overall = "fail" if first_failure else "pass"
        return {
            "status": "success",
            "value": v,
            "unit": unit,
            "overall": overall,
            "stages": stages,
            "first_failure": first_failure,
        }
