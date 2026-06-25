"""Tier-logic tests for app.routers.chat_photos.

The product contract is that the model produces OBSERVATIONS, never
"violations". These tests pin that contract by directly exercising
the tier helpers; the route handler itself is exercised in the
existing chat-photo bridge test.
"""
from __future__ import annotations

from app.routers.chat_photos import (
    _DETECTED_THRESHOLD,
    _LOW_CONF_THRESHOLD,
    _HAT_FRAGMENTS,
    _VEST_FRAGMENTS,
    _person_observation,
    _tier_for,
)

_FORBIDDEN = ("violation", "non-compliant", "non compliant", "breach")


def _check_no_violation_language(msg: str) -> None:
    low = msg.lower()
    for term in _FORBIDDEN:
        assert term not in low, f"forbidden term {term!r} in {msg!r}"


def test_vest_at_high_conf_returns_detected():
    out = _tier_for(
        [{"class": "yellow reflective safety vest", "confidence": 0.71}],
        _VEST_FRAGMENTS,
    )
    assert out["tier"] == "detected"
    assert out["max_confidence"] == 0.71
    assert "vest detected" in out["message"]
    _check_no_violation_language(out["message"])


def test_vest_at_low_conf_returns_low_confidence():
    out = _tier_for(
        [{"class": "high visibility vest", "confidence": 0.09}],
        _VEST_FRAGMENTS,
    )
    assert out["tier"] == "low_confidence"
    assert "low confidence" in out["message"]
    assert "possible" in out["message"]
    _check_no_violation_language(out["message"])


def test_vest_below_floor_returns_not_detected():
    # Anything below _LOW_CONF_THRESHOLD (0.05) gets filtered upstream
    # by the conf=0.05 predict call. Pass an empty list to exercise the
    # not_detected path.
    out = _tier_for([], _VEST_FRAGMENTS)
    assert out["tier"] == "not_detected"
    assert out["message"] == ""
    assert out["max_confidence"] == 0.0


def test_hat_tier_mirrors_vest_thresholds():
    high = _tier_for([{"class": "hard hat", "confidence": 0.55}], _HAT_FRAGMENTS)
    low = _tier_for([{"class": "yellow or white safety helmet", "confidence": 0.20}], _HAT_FRAGMENTS)
    assert high["tier"] == "detected"
    assert low["tier"] == "low_confidence"
    _check_no_violation_language(high["message"])
    _check_no_violation_language(low["message"])


def test_person_observation_fires_only_when_person_confident_and_vest_missing():
    detections = [
        {"class": "person", "confidence": 0.80},
        {"class": "yellow or white safety helmet", "confidence": 0.55},
    ]
    vest_tier = _tier_for(detections, _VEST_FRAGMENTS)  # not_detected
    obs = _person_observation(detections, vest_tier)
    assert obs == "no vest detected in image"
    _check_no_violation_language(obs)


def test_person_observation_suppressed_when_vest_detected():
    detections = [
        {"class": "person", "confidence": 0.80},
        {"class": "yellow reflective safety vest", "confidence": 0.45},
    ]
    vest_tier = _tier_for(detections, _VEST_FRAGMENTS)  # detected
    obs = _person_observation(detections, vest_tier)
    assert obs is None


def test_person_observation_suppressed_when_person_below_threshold():
    # Person at 0.20 is below _DETECTED_THRESHOLD (0.30) -- don't claim
    # "no vest detected in image" when we're not confident a person is
    # even there. This is the precision guard.
    detections = [{"class": "person", "confidence": 0.20}]
    vest_tier = _tier_for(detections, _VEST_FRAGMENTS)
    obs = _person_observation(detections, vest_tier)
    assert obs is None


def test_thresholds_are_what_the_product_spec_says():
    assert _DETECTED_THRESHOLD == 0.30
    assert _LOW_CONF_THRESHOLD == 0.05


def test_class_match_is_case_insensitive_and_substring():
    out = _tier_for([{"class": "ORANGE reflective Safety VEST", "confidence": 0.60}], _VEST_FRAGMENTS)
    assert out["tier"] == "detected"
