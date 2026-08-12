"""extract_measurements must read the drawing, not just answer in the shape.

Audit bar 3, 2026-08-12. This is the one of three unreachable actions that
turned out to be real and was kept wired (the other two cannot run at all --
see tests/test_construction_actions_reachable.py). Wiring it was only half the
job: control-delete showed it had no coverage that could detect it breaking.

Replacing the whole body with

    return {"status": "success", "measurements": [], "specifications": [],
            "count": 0, "confidence": 0}

left tests/test_all_blocks.py and tests/test_blocks_simple.py green -- 24
passed. Both reference the action, but both call it with `{}` and no file, so
they only ever exercise the honest-error branch. The extraction itself was
untested, which is the usual reason an action is unreachable in the first
place: nothing ever ran it.

Measured on the real drawing fixture: 34 measurements, 16 distinct raw
strings, 3 specifications.
"""
from __future__ import annotations

from pathlib import Path

import pytest

DRAWING = Path("tests/fixtures/drawing_tm_200.pdf")

pytestmark = pytest.mark.skipif(
    not DRAWING.is_file(),
    reason=f"real drawing fixture missing at {DRAWING}",
)


@pytest.fixture(scope="module")
def extracted():
    """Parse the PDF once -- it is a real multi-page drawing."""
    import asyncio

    from app.containers.construction import ConstructionContainer

    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        ConstructionContainer().extract_measurements({"file_path": str(DRAWING)}, {})
    )


def test_measurements_come_off_the_drawing(extracted):
    """Non-empty, and varied enough that a constant cannot fake it."""
    assert extracted["status"] == "success", extracted
    measurements = extracted["measurements"]
    assert measurements, "no measurements extracted from a real construction drawing"

    # A stub returning one repeated item would satisfy "non-empty". Distinct
    # source strings can only come from reading different parts of the file.
    distinct = {m.get("raw") for m in measurements}
    assert len(distinct) > 5, (
        f"{len(measurements)} measurements but only {len(distinct)} distinct source "
        "strings -- that is a repeated constant, not an extraction"
    )


def test_count_reconciles_with_the_items(extracted):
    """`count` must describe the list beside it.

    The two fields are returned independently, so a body that stopped
    extracting but kept a count -- or vice versa -- shows up here.
    """
    assert extracted["count"] == len(extracted["measurements"]), extracted["count"]


def test_every_measurement_is_structurally_complete(extracted):
    """Half-populated rows are the failure mode a length check misses."""
    for m in extracted["measurements"]:
        assert {"type", "value", "unit", "item", "raw"} <= set(m), m
        assert isinstance(m["value"], (int, float)) and not isinstance(m["value"], bool), m
        assert m["unit"], m


def test_confidence_is_never_a_placeholder_zero(extracted):
    """Guards the fix made alongside this file.

    `measurement_extraction` is computed nowhere in the codebase, and the old
    default reported that absence as confidence 0 -- indistinguishable, to a
    reader, from a genuine finding of no confidence. Null means not computed.
    If a real score ever lands here it must be a real score.
    """
    confidence = extracted["confidence"]
    assert confidence is None or 0 < confidence <= 1, (
        f"confidence={confidence!r}: a bare 0 is the placeholder this assertion "
        "exists to catch. Return None when nothing computes it."
    )


@pytest.mark.asyncio
async def test_refuses_honestly_with_nothing_to_read():
    """No file and no PDF block must be an explicit refusal, never an empty
    success -- an empty success is what makes a gutted extractor invisible."""
    from app.containers.construction import ConstructionContainer

    result = await ConstructionContainer().extract_measurements({}, {})
    assert result["status"] == "error"
    assert "extract" in result["error"].lower()


@pytest.mark.asyncio
async def test_route_reaches_the_same_extraction():
    """The router path must produce the measurements too. The action was
    unreachable until 2026-08-12, so this direction has never been exercised."""
    from app.containers.construction import ConstructionContainer

    result = await ConstructionContainer().route(
        "extract_measurements", {"file_path": str(DRAWING)},
        {"action": "extract_measurements"},
    )
    assert result["status"] == "success", result
    assert result["count"] > 0, result
