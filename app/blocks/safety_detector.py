"""Fine-tuned YOLOv8 safety + QA/QC detector.

Loads weights produced by ``scripts/run_safety_qaqc_round.py`` (a YOLOv8n
fine-tune over the active classes in ``app/blocks/safety_classes.json``).
Used by ``scripts/infer_photo_metadata.py`` during batch PC inference, and
registered in the block registry for Phase 3 runtime use.

Class names embedded in the YOLO weights file must match names in our
registry (``safety_classes.json``). The YOLO-internal class index is
opaque; we map via ``model.names[idx]`` -> registry lookup.

Env vars
========
- ``SAFETY_DETECTOR_WEIGHTS`` -- path to a YOLO .pt; if unset, ``default_detector()`` returns None.
- ``SAFETY_DETECTOR_CONF``    -- default confidence threshold (0.0-1.0); falls back to 0.25.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

from app.blocks.safety_classes import get_class_by_name

try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None

logger = logging.getLogger(__name__)


class SafetyDetector:
    """Wraps a fine-tuned YOLOv8 .pt and returns structured detections
    mapped onto our registry's stable class IDs."""

    def __init__(self, weights_path: Path) -> None:
        if YOLO is None:
            raise RuntimeError("ultralytics not installed; run pip install -r requirements-cv.txt")
        self._weights_path = Path(weights_path)
        self._model = YOLO(str(self._weights_path))
        self._yolo_names: Dict[int, str] = dict(self._model.names)
        self._yolo_to_registry: Dict[int, int] = {}
        for yolo_idx, name in self._yolo_names.items():
            try:
                self._yolo_to_registry[int(yolo_idx)] = get_class_by_name(name).id
            except KeyError:
                logger.warning("YOLO class %r at index %d not in registry; will be dropped on detection",
                               name, yolo_idx)

    @property
    def weights_path(self) -> Path:
        return self._weights_path

    @property
    def class_names(self) -> List[str]:
        return [self._yolo_names[i] for i in sorted(self._yolo_names)]

    def detect(self, file_path: Path, conf_threshold: float = 0.25) -> List[Dict]:
        """Run detection on one image. Returns a list of dicts:
        ``[{class_id, class, category, confidence, bbox: [x1,y1,x2,y2]}, ...]``
        where ``class_id`` is the registry's stable ID (not the YOLO-internal index).
        Drops any YOLO class that isn't in the registry."""
        results = self._model(str(file_path), conf=conf_threshold, verbose=False)
        if not results:
            return []
        r = results[0]
        if r.boxes is None or len(r.boxes) == 0:
            return []
        cls_indices = r.boxes.cls.tolist()
        confs = r.boxes.conf.tolist()
        boxes = r.boxes.xyxy.tolist()

        out: List[Dict] = []
        for yolo_idx, conf, box in zip(cls_indices, confs, boxes):
            registry_id = self._yolo_to_registry.get(int(yolo_idx))
            if registry_id is None:
                continue
            entry = get_class_by_name(self._yolo_names[int(yolo_idx)])
            out.append({
                "class_id": registry_id,
                "class": entry.name,
                "category": entry.category,
                "confidence": float(conf),
                "bbox": [float(x) for x in box],
            })
        return out


def default_detector() -> Optional[SafetyDetector]:
    """Return a SafetyDetector built from ``SAFETY_DETECTOR_WEIGHTS``,
    or None if the env var is unset or the weights are missing.
    Lets callers gracefully no-op when the model hasn't been trained yet."""
    weights_env = os.getenv("SAFETY_DETECTOR_WEIGHTS")
    if not weights_env:
        return None
    weights = Path(weights_env)
    if not weights.is_file():
        logger.warning("SAFETY_DETECTOR_WEIGHTS=%s not found on disk", weights)
        return None
    try:
        return SafetyDetector(weights)
    except Exception:
        logger.exception("failed to load SafetyDetector from %s", weights)
        return None
