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
            "currency": "SAR",                 # optional; swaps the cost band
            "slack_factor": 2.0,               # optional; empirical slack
            "strict": False,                   # optional; no slack at all
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
            "empirical":   {"pass": bool, "reason": str, "borderline": bool?},
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
#
# Keyed by (material_type, metric, currency). `currency` is "USD" for cost
# metrics, "__none__" for non-cost metrics (temperature, volume, ...). The
# lookup falls back from a currency-specific entry to the USD baseline and
# then to a currency-agnostic 2-tuple form for backwards compat.
#
# Rough FX used to derive the non-USD cost bands (mid-2026):
#   1 USD ≈ 3.75 SAR ≈ 3.67 AED ≈ 0.92 EUR
EMPIRICAL_RANGES: Dict[Tuple[str, str, str], Tuple[float, float]] = {
    # Concrete supply rate.
    ("concrete",  "rate_usd_per_m3", "USD"): (50.0,    500.0),
    ("concrete",  "rate_usd_per_m3", "SAR"): (188.0,   1_875.0),
    ("concrete",  "rate_usd_per_m3", "AED"): (184.0,   1_835.0),
    ("concrete",  "rate_usd_per_m3", "EUR"): (46.0,    460.0),
    # Steel rate per kg.
    ("steel",     "rate_usd_per_kg", "USD"): (0.5,     10.0),
    ("steel",     "rate_usd_per_kg", "SAR"): (1.88,    37.5),
    ("steel",     "rate_usd_per_kg", "AED"): (1.84,    36.7),
    ("steel",     "rate_usd_per_kg", "EUR"): (0.46,    9.2),
    # Rebar rate per kg.
    ("rebar",     "rate_usd_per_kg", "USD"): (0.5,     8.0),
    ("rebar",     "rate_usd_per_kg", "SAR"): (1.88,    30.0),
    ("rebar",     "rate_usd_per_kg", "AED"): (1.84,    29.4),
    ("rebar",     "rate_usd_per_kg", "EUR"): (0.46,    7.36),
    # Formwork rate per m2.
    ("formwork",  "rate_usd_per_m2", "USD"): (10.0,    200.0),
    ("formwork",  "rate_usd_per_m2", "SAR"): (37.5,    750.0),
    ("formwork",  "rate_usd_per_m2", "AED"): (36.7,    734.0),
    ("formwork",  "rate_usd_per_m2", "EUR"): (9.2,     184.0),
    # Generic cost ceiling, per currency.
    ("__any__",   "cost_usd",        "USD"): (0.0,     1e10),
    ("__any__",   "cost_usd",        "SAR"): (0.0,     3.75e10),
    ("__any__",   "cost_usd",        "AED"): (0.0,     3.67e10),
    ("__any__",   "cost_usd",        "EUR"): (0.0,     0.92e10),
    # Non-cost metrics: currency dimension is N/A.
    ("concrete",    "volume_m3",        "__none__"): (1.0,    5_000_000.0),
    ("concrete",    "compressive_mpa",  "__none__"): (10.0,   80.0),
    ("steel",       "weight_kg",        "__none__"): (1.0,    100_000_000.0),
    ("rebar",       "length_m",         "__none__"): (1.0,    50_000_000.0),
    ("excavation",  "volume_m3",        "__none__"): (1.0,    50_000_000.0),
    ("formwork",    "area_m2",          "__none__"): (1.0,    5_000_000.0),
    ("__any__",     "temperature_degc", "__none__"): (-40.0,  500.0),
    ("__any__",     "duration_weeks",   "__none__"): (0.0,    1040.0),
    ("__any__",     "percent",          "__none__"): (0.0,    100.0),
}


# Metrics that are inherently currency-denominated. Everything else is
# physical and uses the "__none__" currency slot.
_COST_METRICS = {"rate_usd_per_m3", "rate_usd_per_kg", "rate_usd_per_m2", "cost_usd"}


def _lookup_range(material: str, metric: str, currency: Optional[str]) -> Optional[Tuple[float, float]]:
    """Return the empirical (min, max) for (material, metric, currency).

    Lookup order:
      1. (material, metric, currency) — exact match if currency supplied.
      2. (material, metric, "USD") — USD baseline for cost metrics.
      3. (material, metric, "__none__") — non-cost metric.
      4. (__any__, metric, currency) → (__any__, metric, "USD") →
         (__any__, metric, "__none__") — generic backstops.
    """
    keys = []
    if currency:
        keys.append((material, metric, currency.upper()))
    keys.append((material, metric, "USD"))
    keys.append((material, metric, "__none__"))
    if currency:
        keys.append(("__any__", metric, currency.upper()))
    keys.append(("__any__", metric, "USD"))
    keys.append(("__any__", metric, "__none__"))
    for k in keys:
        rng = EMPIRICAL_RANGES.get(k)
        if rng:
            return rng
    return None


def _infer_metric(value: Any, unit: Optional[str], ctx: Dict[str, Any]) -> Optional[str]:
    """Best-effort guess of the empirical-range key from unit + material_type.

    Caller can always override by setting `context.metric` explicitly. This
    just covers the common case where the result envelope already carries
    enough info to disambiguate.
    """
    if not unit:
        return None
    u = unit.strip()
    u_lower = u.lower()
    material = (ctx.get("material_type") or "").lower().strip()

    u_compact = u_lower.replace(" ", "")
    if u_compact.endswith("/m3") or u_compact.endswith("/m**3") or u_compact.endswith("/cubicmeter"):
        if material in {"concrete", "excavation"}:
            return "rate_usd_per_m3"
    if u_compact.endswith("/kg"):
        if material in {"steel", "rebar"}:
            return "rate_usd_per_kg"
    if u_compact.endswith("/m2") or u_compact.endswith("/m**2"):
        if material == "formwork":
            return "rate_usd_per_m2"

    if u_lower in {"kg", "kilogram", "kilograms"} and material in {"steel", "rebar"}:
        return "weight_kg"

    if u_lower in {"m3", "m**3", "cubicmeter", "cubic_meter", "cubic meters"}:
        if material in {"concrete", "excavation"}:
            return "volume_m3"

    if u_lower in {"m2", "m**2", "squaremeter", "square_meter", "square meters"} and material == "formwork":
        return "area_m2"

    if u_lower in {"m", "meter", "meters"} and material == "rebar":
        return "length_m"

    if u_lower in {"mpa", "megapascal"} and material == "concrete":
        return "compressive_mpa"

    if u_lower in {"degc", "celsius", "delta_degc", "deg c", "°c"} or u_lower.endswith("degc"):
        return "temperature_degc"

    if u_lower in {"weeks", "week"} and ctx.get("duration_weeks") is not None:
        return "duration_weeks"

    if u_lower in {"%", "percent", "pct"}:
        return "percent"

    return None


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
    """Rough industry sanity range with a tunable slack band.

    Slack model:
      * `slack_factor` (default 2.0): values inside `[emin/slack, emax*slack]`
        pass but are flagged borderline if outside `[emin, emax]`.
      * `strict=True`: slack disabled — anything outside `[emin, emax]` fails.

    The previous implementation hard-coded a 5× slack, which let 1,500 USD/m³
    concrete pass as "borderline" (3× the high end). Default 2× catches that
    while still tolerating regional pricing wiggle.
    """
    emin = ctx.get("empirical_min")
    emax = ctx.get("empirical_max")
    if emin is None or emax is None:
        material = (ctx.get("material_type") or "").lower().strip()
        metric = (ctx.get("metric") or "").lower().strip()
        currency = ctx.get("currency")
        rng = _lookup_range(material, metric, currency)
        if rng:
            emin = emin if emin is not None else rng[0]
            emax = emax if emax is not None else rng[1]
        else:
            return {"pass": True, "reason": "no empirical range for (material, metric) — skipped"}
    try:
        v = float(value)
    except Exception:
        return {"pass": False, "reason": "value not coercible to float for empirical check"}

    strict = bool(ctx.get("strict"))
    try:
        slack = float(ctx.get("slack_factor", 2.0))
    except (TypeError, ValueError):
        slack = 2.0
    if slack < 1.0:
        slack = 1.0

    if strict:
        if v < emin or v > emax:
            return {"pass": False, "reason": f"value {v} outside empirical range [{emin}, {emax}] (strict)"}
        return {"pass": True, "reason": f"value {v} within empirical range [{emin}, {emax}] (strict)"}

    if emin > 0 and emax > 0:
        slack_lo = emin / slack
        slack_hi = emax * slack
    else:
        # Range spans or sits below zero (e.g. temperature -40..500). Widen
        # additively by `(slack - 1)` of the span on each side so 2× slack
        # = "twice the width," matching the multiplicative case for spans
        # starting near zero.
        width = emax - emin
        slack_lo = emin - width * (slack - 1.0)
        slack_hi = emax + width * (slack - 1.0)

    if v < slack_lo:
        return {"pass": False, "reason": f"value {v} far below empirical range [{emin}, {emax}] ({slack}x slack {slack_lo})"}
    if v > slack_hi:
        return {"pass": False, "reason": f"value {v} far above empirical range [{emin}, {emax}] ({slack}x slack {slack_hi})"}
    if v < emin or v > emax:
        return {
            "pass": True,
            "borderline": True,
            "reason": f"value {v} outside tight empirical range [{emin}, {emax}] but within {slack}x slack — borderline",
        }
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
    version = "1.1.0"
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

        # Params can promote slack_factor / strict / currency without
        # forcing every caller to nest them under `context`.
        for k in ("slack_factor", "strict", "currency", "metric", "material_type"):
            if k not in ctx and k in params:
                ctx[k] = params[k]

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

        # Infer metric from unit + material_type when caller didn't spell
        # it out. Runs before the physical check so temperature deltas get
        # the negative-allowed floor.
        if not ctx.get("metric"):
            inferred = _infer_metric(v, unit, ctx)
            if inferred:
                ctx["metric"] = inferred
                ctx.setdefault("_metric_inferred", True)

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
        result = {
            "status": "success",
            "value": v,
            "unit": unit,
            "overall": overall,
            "stages": stages,
            "first_failure": first_failure,
        }
        if empirical.get("borderline"):
            result["borderline"] = True
        if ctx.get("_metric_inferred"):
            result["metric_inferred"] = ctx.get("metric")
        return result
