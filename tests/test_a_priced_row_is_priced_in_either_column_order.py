"""Rate and quantity on the row means there is an amount. In either order.

Live d8d9573, master_corpus, 0/5:

    "What is the amount for removal of existing chain link fence (D549.2)?"

    -> "the chain link fence item (D549.2) is **not priced** in the sources
        shown -- it appears as a rate-only or excluded item"

The row was retrieved at rank 1, and it reads:

    G | Breakout and remove existing chain link fence  D549.2  m  3,504
        80.00  280,320.00
    H | Breakout and remove existing storm water culverts  D529.3  m
        1,370.00  Rate Only

Owner's ruling on this exact item: "rate only is when you have no quantity;
if you have rate and quantity, you must have an amount."

A deterministic composer exists to state such a row without asking the model
to read it (WAVE 2 B5). It parsed nothing, so the model read the row itself
and attached the NEIGHBOUR's "Rate Only" to the fence. The parser expected

    <qty> <unit> <rate> <amount>        3,504 m 80.00 280,320.00

and a CESMM bill prints its columns  Ref | Unit | Qty | Rate | Amount:

    <unit> <qty> <rate> <amount>        m 3,504 80.00 280,320.00

Accepting the second order is safe for the same reason the first is: a triple
only counts when qty x rate equals the printed amount.
"""
from __future__ import annotations

import pytest

from app.core.rag.retriever import (
    chunk_states_priced_item,
    chunk_states_rate_only_item,
    compose_priced_boq_row,
)

ASK = "What is the amount for removal of existing chain link fence (D549.2)?"
CODES = ["d549.2"]

# Row shape exactly as indexed; item figures kept (they are arithmetic, not
# identity), party names never present.
UNIT_FIRST = (
    "119 335.00 39,865.00} F |Breakout and remove existing metal beam guard rail "
    "D549.1 m240.00 Rate Only G |Breakout and remove existing chain link fence "
    "D549.2 m 3,504 80.00 280,320.00 H_ |Breakout and remove existing storm water "
    "culverts D529.3 m 1,370.00 Rate Only} fl stamp noise"
)
EXCLUDED_ELSEWHERE = (
    "r 74 4,000 296,000.00 G_ |Breakout and remove existing metal beam guard rail "
    "D 549.1 sum 1 Excluded H_ {Breakout and remove existing chain link fence "
    "D 549.2 | sum 1 Excluded J |Breakout and remove existing storm water culverts "
    "D599.5 | sum 1 Excluded"
)
SCOPE_LIST = (
    "D 549.1 - Breakout and remove existing metal beam guard rail D 549.2 - "
    "Breakout and remove existing chain link fence D 599.5 - Breakout and remove"
)


def test_the_live_row_is_priced():
    assert chunk_states_priced_item(UNIT_FIRST, CODES)


def test_the_neighbours_rate_only_does_not_rub_off():
    """D549.1 above it and D529.3 below it ARE Rate Only. D549.2 is not."""
    assert not chunk_states_rate_only_item(UNIT_FIRST, CODES)
    assert chunk_states_rate_only_item(UNIT_FIRST, ["d549.1"])
    assert chunk_states_rate_only_item(UNIT_FIRST, ["d529.3"])


def test_compose_states_quantity_rate_and_amount():
    row = compose_priced_boq_row(ASK, UNIT_FIRST)
    assert row, "the composer parsed nothing from a fully priced row"
    assert (row["qty"], row["rate"], row["amount"]) == (3504.0, 80.0, 280320.0)
    assert row["unit"].lower() == "m"
    assert row["code"].lower() == "d549.2"


@pytest.mark.parametrize("order", ["priced_first", "excluded_first"])
def test_the_priced_row_wins_over_an_excluded_sibling_in_another_bill(order):
    """Live pool: the same code is "sum 1 Excluded" in a different bill
    section. Whichever arrives first, the row that is priced is the answer."""
    parts = [UNIT_FIRST, SCOPE_LIST, EXCLUDED_ELSEWHERE]
    if order == "excluded_first":
        parts.reverse()
    row = compose_priced_boq_row(ASK, "\n\n".join(parts))
    assert row and row["amount"] == 280320.0


@pytest.mark.parametrize(
    "row",
    [
        "D549.2 m 3,504 80.00 280,320.00",          # unit qty rate amount
        "D549.2 3,504 m 80.00 280,320.00",          # qty unit rate amount (old)
        "D549.2 m3,504 80.00 280,320.00",           # OCR glued the unit
        "D549.2 | m | 3,504 | 80.00 | 280,320.00",  # table pipes
        "D549.2 m2 1,000 12.50 12,500.00",          # another unit
        "D549.2 Nr 48 220.00 10,560.00",
    ],
)
def test_every_column_order_and_ocr_variant_parses(row):
    assert chunk_states_priced_item("Breakout fence " + row, CODES), row


@pytest.mark.parametrize(
    "row",
    [
        # The arithmetic is the guard: these are not priced rows.
        "D549.2 m 3,504 80.00 999,999.00",    # amount does not equal qty x rate
        "D549.2 m 1,370.00 Rate Only",        # a rate and no quantity
        "D549.2 m 3,504 Rate Only",
        "D549.2 sum 1 Excluded",
        "D549.2 - Breakout and remove existing chain link fence",
    ],
)
def test_a_row_that_is_not_priced_is_never_made_priced(row):
    assert not chunk_states_priced_item("Breakout fence " + row, CODES), row
    assert compose_priced_boq_row(ASK, "Breakout fence " + row) is None
