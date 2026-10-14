"""A zero the user never wrote is not a figure they gave.

Lane 2 on 9c1d155: the crane calculator answered "The number of cranes given
(0) is outside ..." to an ask that states no count: the model sent 0. A zero
the user's words never state, sent to an input that cannot be zero, is
dropped, so the calculator asks for the input. A stated zero ("0 cranes",
"no cranes", "zero", "nil") stays and meets the range check, as before.
Invented asks.
"""
from app.lib import construction_formulas as cf

BASE = {"crane_capacity_tons": 50, "duration_months": 6}


def _run(ask, **params):
    return cf.run_calculation("crane_cost_estimate", {"text": ask, **BASE, **params})


def test_an_invented_zero_is_asked_for_not_refused_as_given():
    out = _run("Crane cost for a 50 t mobile crane over 6 months?", num_cranes=0)
    assert out["status"] == "error"
    assert "given (0)" not in str(out.get("error") or "")
    assert "num_cranes" in str(out.get("error") or "")


def test_a_stated_zero_still_meets_the_range_check():
    out = _run("Crane cost with 0 cranes, 50 t, 6 months?", num_cranes=0)
    assert "given (0)" in str(out.get("error") or "")


def test_a_real_count_still_runs():
    assert _run("Crane cost for 2 cranes of 50 t over 6 months?", num_cranes=2)["status"] == "success"


def test_zero_words_are_found_and_others_are_not():
    assert cf._ZERO_WORDS.search("no cranes yet") and cf._ZERO_WORDS.search("rate 10.0 and 0 cranes")
    assert not cf._ZERO_WORDS.search("a 50 t crane for 6 months at 1,500 per day, 10.05 m reach")
