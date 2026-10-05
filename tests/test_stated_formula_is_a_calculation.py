"""A question that states its own formula and inputs is a calculation.

"0.613V^2 for 40 m/s", "wL^2/8 for a 6 m span": the user gave the expression
and the value, so the turn goes to the calculator. A unit written with a power
("per m^2", "kN/m^2") is not a formula. Synthetic asks only.
"""
from __future__ import annotations

import pytest

from app.agents.runtime import _message_is_formula_style_ask


@pytest.mark.parametrize("ask", [
    "What velocity pressure does q = 0.613V^2 give for a gust of 35 m/s?",
    "Using wL^2/8, what is the moment for a 7 m span carrying 12 kN/m?",
    "What is 0.5*rho*V**2 for an air speed of 22 m/s?",
    "Deflection by 5wL^4/384EI for a 9 m span, w = 6 kN/m?",
])
def test_a_stated_formula_with_an_input_is_a_calculation(ask):
    assert _message_is_formula_style_ask(ask)


@pytest.mark.parametrize("ask", [
    "What is the plaster rate per m^2 in the bill?",
    "Which drawing shows the 25 kN/m^2 imposed load?",
    "What does V^2 mean in the wind section of the specification?",
])
def test_a_unit_power_or_a_formula_without_input_is_not(ask):
    assert not _message_is_formula_style_ask(ask)
