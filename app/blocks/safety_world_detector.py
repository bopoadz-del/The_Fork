"""YOLO-Worldv2 detector with a pre-baked prompt vocabulary.

PRODUCT NAME: "Safety Observation AI v2". The model surfaces what it
SEES (vest, hat, person, crack...). It does NOT and MUST NOT label
anything as a "violation", "non-compliance", or "PPE breach" -- those
are judgments the application layer makes with the operator in the
loop.

The .onnx file at SAFETY_WORLD_WEIGHTS was produced by
``scripts/bake_world_model.py`` followed by an ONNX export. The bake
step calls ``YOLOWorld.set_classes(prompts)`` (CLIP runs there) then
saves; the export step writes a self-contained .onnx with the CLIP-
derived text vectors reparameterized into the classifier head. The
.onnx loads with plain ``YOLO()`` -- no CLIP import is needed at
inference time, which is why this module never touches CLIP.

Detections are returned with the original prompt strings (e.g.
"high visibility vest") rather than registry ids, because the class
list is defined by the prompt JSON, not by safety_classes.json.

Env vars
========
- ``SAFETY_WORLD_WEIGHTS``  path to baked .onnx; if unset, default_detector() returns None
- ``SAFETY_WORLD_CONF``     default confidence threshold (0.0-1.0); falls back to 0.05
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None

logger = logging.getLogger(__name__)


class SafetyWorldDetector:
    def __init__(self, weights_path: Path) -> None:
        if YOLO is None:
            raise RuntimeError("ultralytics not installed")
        self._weights_path = Path(weights_path)
        self._model = YOLO(str(self._weights_path))
        # YOLO reads the baked class names directly from the .pt -- these
        # are the same prompt strings passed to set_classes() at bake time.
        self._names: Dict[int, str] = dict(self._model.names)
        # Load companion manifest if it exists, purely for provenance.
        manifest = self._weights_path.with_suffix(".prompts.json")
        if manifest.is_file():
            try:
                self._manifest = json.loads(manifest.read_text(encoding="utf-8"))
            except Exception:
                self._manifest = None
        else:
            self._manifest = None

    @property
    def weights_path(self) -> Path:
        return self._weights_path

    @property
    def class_names(self) -> List[str]:
        return [self._names[i] for i in sorted(self._names)]

    @property
    def manifest(self) -> Optional[dict]:
        return self._manifest

    def detect(self, file_path: Path, conf_threshold: float = 0.25) -> List[Dict]:
        """Run detection. Returns a list of dicts:
            [{class: "high visibility vest", confidence: 0.71, bbox: [x1,y1,x2,y2]}, ...]
        """
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
        for idx, conf, box in zip(cls_indices, confs, boxes):
            name = self._names.get(int(idx))
            if name is None:
                continue
            out.append({
                "class": name,
                "confidence": float(conf),
                "bbox": [float(x) for x in box],
            })
        return out


def default_detector() -> Optional[SafetyWorldDetector]:
    weights_env = os.getenv("SAFETY_WORLD_WEIGHTS")
    if not weights_env:
        return None
    weights = Path(weights_env)
    if not weights.is_file():
        logger.warning("SAFETY_WORLD_WEIGHTS=%s not found on disk", weights)
        return None
    try:
        return SafetyWorldDetector(weights)
    except Exception:
        logger.exception("failed to load SafetyWorldDetector from %s", weights)
        return None
