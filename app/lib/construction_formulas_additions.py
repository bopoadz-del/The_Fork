"""
The Fork -- Additional Construction Formulas

New calculators added post-live-chat-test to cover gaps found
in the 13-query battery. These supplement the existing
construction_formulas.py CALCULATORS registry.

Each formula returns a dict with deterministic values.
No fabrication -- all values traceable to standards.
"""

from __future__ import annotations


def guardrail_top_rail_height() -> dict:
    """
    OSHA guardrail top-rail height for fall protection.

    OSHA 29 CFR 1926.502(b): top edge height of top rails shall be
    42 inches +/- 3 inches (1.07 m +/- 76 mm) above the walking/working
    level. Mid-rails at halfway point.

    Returns dict with heights in imperial and metric.
    """
    return {
        "top_rail_height_in": 42,
        "top_rail_height_mm": 1067,
        "top_rail_height_m": 1.07,
        "mid_rail_height_in": 21,
        "mid_rail_height_mm": 533,
        "mid_rail_height_m": 0.53,
        "tolerance_in": 3,
        "tolerance_mm": 76,
        "standard": "OSHA 29 CFR 1926.502(b)",
        "scope": "construction",
        "note": (
            "Top rail 42 in +/- 3 in above walking/working level. "
            "General industry (1910.29) same requirement. "
            "Saudi Arabia: SABIC/Aramco typically adopt OSHA standards."
        ),
    }


def calculate_interim_payment(
    gross_valuation: float | int,
    retention_percent: float | int = 10.0,
) -> dict:
    """
    Calculate interim payment after retention deduction.

    FIDIC Red Book Clause 14.3: the Engineer issues an Interim Payment
    Certificate within 28 days of receiving the Statement. Retention is
    deducted per Clause 14.3 unless the limit (typically 5% of Accepted
    Contract Amount) has been reached.

    Args:
        gross_valuation: Certified gross amount for this period.
        retention_percent: Retention percentage (default 10%).

    Returns:
        dict with gross, retention, net payment, and breakdown.
    """
    gross = float(gross_valuation)
    retention_rate = float(retention_percent) / 100.0
    retention_amount = gross * retention_rate
    net_payment = gross - retention_amount

    return {
        "gross_valuation": round(gross, 2),
        "retention_percent": float(retention_percent),
        "retention_amount": round(retention_amount, 2),
        "net_payment": round(net_payment, 2),
        "calculation": f"{gross} - ({gross} x {retention_percent}%) = {round(net_payment, 2)}",
        "standard": "FIDIC Red Book Clause 14.3",
        "note": (
            "Retention default 10%; FIDIC cap is 5% of Accepted Contract Amount. "
            "Verify if retention cap has been reached on this project."
        ),
    }


# -- Registry for auto-import ---------------------------------------------

ADDITIONAL_CALCULATORS = {
    "guardrail_top_rail_height": guardrail_top_rail_height,
    "calculate_interim_payment": calculate_interim_payment,
}
