"""End-to-end checks against REAL photographs, when they are present locally.

The synthetic tests in test_photo_observations.py pin the contract but cannot
tell whether the model is right about an image. These run the actual ONNX over
actual construction photographs.

They SKIP (never fail-open) when the photographs or the detector runtime are
absent, so the main CI test run needs no network and no torch. Fetch the
photographs with:

    python scripts/fetch_eval_photos.py

CI's production-like job runs this file in a dedicated step that fetches the
photographs (cached), installs the CPU detector runtime the Dockerfile ships,
and sets ``PHOTO_EVAL_REQUIRED=1`` -- under which every "absent" skip below
becomes a FAILURE, so these tests cannot quietly stop running there.

Measured behaviour for these specific images is recorded in
docs/PHOTO_INTELLIGENCE_EVAL.md. Deliberately, the assertions here do NOT pin
per-class confidences: those move with any re-bake, and a test that fails
whenever the model improves is a test people delete. What is pinned is the
contract — shape, tiering, no promotion, honest empties.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.containers.construction.photo_observations import (
    LOW_CONF_THRESHOLD,
    equipment_from_photos,
    quality_observations,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "photos"
WEIGHTS = Path("data/models/safety_world_v2.onnx")

def _required() -> bool:
    return os.getenv("PHOTO_EVAL_REQUIRED", "").strip() == "1"


def _absent(reason: str):
    """Skip for a missing prerequisite -- or fail, where CI requires it."""
    if _required():
        pytest.fail(f"PHOTO_EVAL_REQUIRED=1 but {reason}")
    pytest.skip(reason)


@pytest.fixture(scope="module", autouse=True)
def _photographs_present():
    if not FIXTURES.is_dir() or not any(FIXTURES.glob("*.jpg")):
        _absent("eval photographs absent — run `python scripts/fetch_eval_photos.py` "
                "(network); these fixtures are deliberately not committed")


@pytest.fixture(scope="module")
def detector():
    if not WEIGHTS.is_file():
        _absent(f"detector weights not found at {WEIGHTS}")
    from app.blocks.safety_world_detector import default_detector

    # Scoped, not os.environ.setdefault: a module fixture that writes the
    # process environment leaks SAFETY_WORLD_WEIGHTS into every later test.
    with pytest.MonkeyPatch.context() as mp:
        if not os.getenv("SAFETY_WORLD_WEIGHTS"):
            mp.setenv("SAFETY_WORLD_WEIGHTS", str(WEIGHTS))
        det = default_detector()
    if det is None:
        _absent("default_detector() returned None — detector runtime "
                "(ultralytics/onnxruntime) not installed or weights not resolving")
    return det


def _analyse(detector, name):
    path = FIXTURES / name
    if not path.is_file():
        _absent(f"fixture {name} not downloaded")
    return [{
        "photo": path.name,
        "detections": detector.detect(path, conf_threshold=LOW_CONF_THRESHOLD),
        "detector": "safety_world_v2",
        "safety_compliance": "unknown",
        "activities_detected": [], "headcount_estimate": 0, "progress_indicators": "",
    }]


def test_ladder_photo_yields_a_temporary_works_record(detector):
    """A photograph of a ladder produces a ladder record, categorised correctly.

    The strongest and least ambiguous detection in the whole eval set (0.962),
    so this is the one real-image assertion safe to make on class identity.
    """
    equipment = equipment_from_photos(_analyse(detector, "e04_ladder_peabody.jpg"))

    items = {e["item"]: e for e in equipment}
    assert "ladder" in items, f"expected a ladder record, got {list(items)}"
    assert items["ladder"]["category"] == "temporary_works"
    assert items["ladder"]["confidence_tier"] == "detected"
    assert items["ladder"]["seen_in_photos"] == ["e04_ladder_peabody.jpg"]


def test_every_observation_is_well_formed_and_traceable(detector):
    """Whatever comes out must carry its evidence: photo, class, confidence, tier."""
    analysis = _analyse(detector, "q11_cracked_tile_flooring.jpg")
    observations = quality_observations(analysis)

    assert observations, "expected at least one observation on this image"
    for obs in observations:
        assert obs["photo"] == "q11_cracked_tile_flooring.jpg"
        assert obs["detected_class"]
        assert LOW_CONF_THRESHOLD <= obs["confidence"] <= 1.0
        assert obs["confidence_tier"] in ("detected", "low_confidence")
        # tier and wording must agree — this is what stops a low-confidence
        # detection reading as a statement of fact about a client's concrete
        if obs["confidence_tier"] == "low_confidence":
            assert obs["observation"].startswith("possible ")
            assert obs["observation"].endswith("-- low confidence")
        else:
            assert obs["observation"].endswith(" detected")


def test_no_verdict_language_survives_the_real_pipeline(detector):
    """The observations-not-verdicts contract, enforced on real detector output."""
    banned = ("violation", "non-compliance", "non-compliant", "defect",
              "breach", "unsafe", "fail")
    for name in ("q11_cracked_tile_flooring.jpg", "e04_ladder_peabody.jpg"):
        analysis = _analyse(detector, name)
        for obs in quality_observations(analysis) + equipment_from_photos(analysis):
            text = obs["observation"].lower()
            assert not any(w in text for w in banned), obs["observation"]


def test_a_photo_with_nothing_detectable_yields_an_honest_empty(detector):
    """q08 produced zero detections in the eval — it must produce zero
    observations, not a hedge or a placeholder."""
    analysis = _analyse(detector, "q08_peeling_paint_bathroom.jpg")
    # The weights are committed, so "no detections on q08" is a fixed fact of
    # this build, not luck. A retrained model that finds something here must
    # fail this precondition (pick another empty photo), not skip the test.
    assert not analysis[0]["detections"], (
        "safety_world_v2.onnx now detects something in q08; choose a photo "
        "the committed model returns no detections for")
    assert quality_observations(analysis) == []
    assert equipment_from_photos(analysis) == []
