"""An out-of-range percentage for a fraction input is quoted as the user
wrote it: 150% reads 150%, not 1.5%. Synthetic calculator."""
from __future__ import annotations

from tests.lane5_core_support import Param, registered


def _share(amount: float, share: float) -> dict:
    return {"value": amount * share}


def test_an_out_of_range_percentage_is_quoted_as_written():
    from app.lib.construction_formulas import run_calculation

    with registered("lane5_share_gate", _share, "Qx share", {
        "amount": Param("currency", lo=0, hi=1e12, label="amount"),
        "share": Param("fraction", lo=0, hi=1, label="share"),
    }, {"value": "currency"}):
        env = run_calculation("lane5_share_gate", {"text": "qx share of 500,000 at 150%",
                                                   "amount": 500000, "share": 150})
    assert env.get("needs_input") is True, env
    assert "150%" in env["question"] and "1.5%" not in env["question"]
    assert "0% to 100%" in env["question"]
