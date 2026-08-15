"""A BOQ line must be classified by what it IS, not by a trailing keyword.

`categorize` walked an ordered list and returned the FIRST pattern that matched
anywhere in the description. A description mentions its substrate as well as its
work -- "plaster render to blockwork walls" names blockwork, "waterproofing to
wet area floors" names floors -- so whichever category happened to sit earlier
in the list won, regardless of which word was the actual subject.

Measured on a real interior bill (2026-08-15):
  "15mm cement plaster render to blockwork walls"   -> Masonry/Blockwork (88/m2)
  "Cementitious waterproofing membrane to wet area floors" -> Finishes (38/m2)

Both priced as the wrong trade.

The head noun of a BOQ line comes FIRST -- that is how bills are written. So
the category whose pattern matches EARLIEST wins, with list order as the
tie-break. The one idiom that breaks the rule is "reinforced concrete", where
the leading word qualifies the following noun rather than naming the work, and
it is excluded explicitly.
"""
from __future__ import annotations

import pytest

from app.lib.boq_pricing import categorize


@pytest.mark.parametrize("desc,expected", [
    # the two measured failures
    ("15mm cement plaster render to blockwork walls, two coats", "Finishes"),
    ("Cementitious waterproofing membrane to wet area floors",
     "Waterproofing/Insulation"),
    # the substrate is named second and must not win
    ("Two coats emulsion paint to plastered blockwork walls", "Finishes"),
    ("Ceramic wall tiles to blockwork partitions", "Finishes"),
    ("Damp proof membrane under concrete slab", "Waterproofing/Insulation"),
])
def test_the_work_wins_over_the_substrate(desc, expected):
    assert categorize(desc) == expected, desc


@pytest.mark.parametrize("desc,expected", [
    # "reinforced concrete" is an idiom: the work is concrete, not rebar
    ("Reinforced concrete slab 300mm thick", "Concrete"),
    ("Reinforced concrete core walls; 400mm", "Concrete"),
    # but real rebar lines still classify as reinforcement
    ("Bar reinforcement to raft slab", "Reinforcement"),
    ("High tensile steel bar reinforcement, 16mm", "Reinforcement"),
    ("Reinforcement mesh A252 to ground slab", "Reinforcement"),
])
def test_reinforced_concrete_is_concrete_but_rebar_is_rebar(desc, expected):
    assert categorize(desc) == expected, desc


@pytest.mark.parametrize("desc,expected", [
    # regressions: these classified correctly before and must not move
    ("Basement excavation; bulk excavation", "Earthworks/Excavation"),
    ("Excavation to receive foundation; raft", "Earthworks/Excavation"),
    ("Remove excavated materials; disposed-off site", "Earthworks/Excavation"),
    ("Concrete blinding; 50mm thick", "Concrete"),
    ("Sand and cement screed under raft slab; 50mm", "Concrete"),
    ("Formwork to suspended slab soffit", "Formwork"),
    ("200mm thick solid concrete blockwork to internal partitions",
     "Masonry/Blockwork"),
    ("Solid core timber door 900x2100mm complete with frame",
     "Windows/Doors/Facade"),
    ("Gypsum board suspended ceiling on metal grid", "Finishes"),
    ("Porcelain floor tiles 800x800mm to lobbies", "Finishes"),
])
def test_previously_correct_lines_do_not_regress(desc, expected):
    assert categorize(desc) == expected, desc


def test_an_unrecognised_line_is_flagged_not_guessed():
    """Never silently bucket an unknown into a priced category."""
    assert categorize("Provide bespoke sculptural artwork") == "Other/Uncategorized"
