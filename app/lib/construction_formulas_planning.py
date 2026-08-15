"""Planning / CPM calculators (additive library, gap-fill).

Critical-path float from an activity's early/late dates. Code-agnostic.
"""
from __future__ import annotations


def critical_path_float(
    early_start: float,
    early_finish: float,
    late_start: float,
    late_finish: float,
) -> dict:
    """Total float TF = LS - ES = LF - EF; the activity is on the critical path
    when TF <= 0. (Free float needs the successor's ES; not computed here.)

    NEGATIVE float is critical too -- MORE critical, not less: a real 2013 P6
    baseline surfaced an activity at TF = -8.6 days (behind its constraint
    dates) that the old `TF == 0` test reported as NOT critical. On a live
    schedule, negative-float activities are exactly the ones driving the
    forecast delay; a planner asking "is this critical?" must never be told
    no about one of them."""
    es, ef = float(early_start), float(early_finish)
    ls, lf = float(late_start), float(late_finish)
    tf = ls - es
    tf_check = lf - ef
    on_critical = tf < 1e-9
    behind = tf < -1e-9
    return {
        "total_float": round(tf, 3),
        "is_critical": on_critical,
        "consistency_check_lf_minus_ef": round(tf_check, 3),
        "standard": "CPM (PMBOK / planning)",
        "note": (f"TF = LS - ES = {ls} - {es} = {tf:.1f} (= LF - EF = {lf} - {ef} "
                 f"= {tf_check:.1f}); "
                 + ("NEGATIVE float - critical and behind schedule."
                    if behind else
                    ("critical" if on_critical else "has float") + ".")),
    }


ADDITIONAL_CALCULATORS = {
    "critical_path_float": critical_path_float,
}
