"""Tests for app.blocks.safety_detector."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def fake_weights(tmp_path: Path) -> Path:
    p = tmp_path / "weights.pt"
    p.write_bytes(b"fake")
    return p


def _make_fake_yolo_with(class_names: dict, cls_indices, confs, boxes):
    """Build a fake ultralytics YOLO model whose call returns scripted detections."""
    fake_box = MagicMock()
    fake_box.cls.tolist.return_value = cls_indices
    fake_box.conf.tolist.return_value = confs
    fake_box.xyxy.tolist.return_value = boxes
    fake_box.__len__.return_value = len(cls_indices)

    fake_result = MagicMock()
    fake_result.boxes = fake_box

    fake_yolo = MagicMock(return_value=[fake_result])
    fake_yolo.names = class_names
    return fake_yolo


def test_detect_returns_registry_ids_and_categories(fake_weights, tmp_path):
    img = tmp_path / "img.jpg"
    img.write_bytes(b"fake")

    fake_yolo = _make_fake_yolo_with(
        class_names={0: "no_hardhat", 1: "concrete_crack"},
        cls_indices=[0, 1],
        confs=[0.85, 0.72],
        boxes=[[10, 20, 30, 40], [50, 60, 70, 80]],
    )

    from app.blocks.safety_detector import SafetyDetector
    with patch("app.blocks.safety_detector.YOLO", return_value=fake_yolo):
        det = SafetyDetector(fake_weights)
        out = det.detect(img, conf_threshold=0.4)

    assert len(out) == 2
    assert out[0]["class"] == "no_hardhat"
    assert out[0]["class_id"] == 0
    assert out[0]["category"] == "safety"
    assert out[0]["confidence"] == 0.85
    assert out[0]["bbox"] == [10.0, 20.0, 30.0, 40.0]
    assert out[1]["class"] == "concrete_crack"
    assert out[1]["class_id"] == 3
    assert out[1]["category"] == "qaqc"


def test_detect_drops_yolo_classes_not_in_registry(fake_weights, tmp_path):
    img = tmp_path / "img.jpg"
    img.write_bytes(b"fake")

    fake_yolo = _make_fake_yolo_with(
        class_names={0: "no_hardhat", 1: "totally_unknown_class"},
        cls_indices=[0, 1],
        confs=[0.85, 0.72],
        boxes=[[10, 20, 30, 40], [50, 60, 70, 80]],
    )

    from app.blocks.safety_detector import SafetyDetector
    with patch("app.blocks.safety_detector.YOLO", return_value=fake_yolo):
        det = SafetyDetector(fake_weights)
        out = det.detect(img, conf_threshold=0.4)

    assert len(out) == 1
    assert out[0]["class"] == "no_hardhat"


def test_detect_returns_empty_when_no_boxes(fake_weights, tmp_path):
    img = tmp_path / "img.jpg"
    img.write_bytes(b"fake")

    fake_result = MagicMock()
    fake_result.boxes = None
    fake_yolo = MagicMock(return_value=[fake_result])
    fake_yolo.names = {0: "no_hardhat"}

    from app.blocks.safety_detector import SafetyDetector
    with patch("app.blocks.safety_detector.YOLO", return_value=fake_yolo):
        det = SafetyDetector(fake_weights)
        out = det.detect(img)

    assert out == []


def test_default_detector_returns_none_when_env_unset(monkeypatch):
    monkeypatch.delenv("SAFETY_DETECTOR_WEIGHTS", raising=False)
    from app.blocks.safety_detector import default_detector
    assert default_detector() is None


def test_default_detector_returns_none_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("SAFETY_DETECTOR_WEIGHTS", str(tmp_path / "nonexistent.pt"))
    from app.blocks.safety_detector import default_detector
    assert default_detector() is None
