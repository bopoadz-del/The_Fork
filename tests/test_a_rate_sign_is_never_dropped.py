"""A per-day rate gives days, and a minus sign the user wrote is never dropped.

Order 0345Z (LANE2 on 1746046, 3/3): "Plastering 4800 m2, gang does -120 m2
per day. How many days?" -- the model passed productivity=120 (sign lost),
the unit words written on "-120" were never matched to 120, and the answer
read "manhours required 40 h". A negative rate is invalid input: it is
refused in the input's own words and asked for again, never normalised.
"""
from app.lib import construction_formulas as cf

NEGATIVE = "Plastering 4800 m2, gang does -120 m2 per day. How many days?"
POSITIVE = "Plastering 4800 m2, gang does 120 m2 per day. How many days?"


def _run(ask, **params):
    return cf.run_calculation("productivity_manpower_duration", {"text": ask, **params})


def test_quantity_and_a_rate_per_day_give_days_not_man_hours():
    out = _run(POSITIVE, quantity=4800, productivity=120)["result"]
    assert out.get("duration") == 40.0
    assert "manhours_required" not in out or out.get("manhours_required") is None


def test_a_dropped_minus_sign_is_restored_and_the_rate_refused():
    out = _run(NEGATIVE, quantity=4800, productivity=120)
    assert out["status"] == "error"
    assert "-120 was given" in out["error"] and "greater than zero" in out["error"]
    assert out.get("needs_input") and "daily_production" in out.get("missing", [])
    assert "manhours" not in str(out).lower() or "manhours_required" not in str(out.get("result") or "")


def test_a_negative_rate_passed_as_written_is_refused_too():
    out = _run(NEGATIVE, quantity=4800, productivity=-120)
    assert out["status"] == "error" and "greater than zero" in out["error"]


def test_a_range_dash_is_not_a_minus_sign():
    assert cf._written_only_negative(150, "between 120-150 m2 per day") is False
    assert cf._written_only_negative(120, "gang does -120 m2 per day") is True
    assert cf._written_only_negative(120, "120 m2 per day, not -120") is False
