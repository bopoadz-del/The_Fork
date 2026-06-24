"""Run a trained YOLO safety/QA-QC model on a folder of photos.

Outputs per-photo detections and a per-class summary across the corpus.
Used after a training round to spot-check what the model actually sees
on the operator's real photos before pushing metadata to Render.

Usage:
    python scripts/test_model_on_corpus.py [--weights data/models/safety_qaqc_v1_r3.pt] [--folder data/training/raw_photos] [--conf 0.25]
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--weights", type=Path, default=Path("data/models/safety_qaqc_v1_r3.pt"))
    p.add_argument("--folder", type=Path, default=Path("data/training/raw_photos"))
    p.add_argument("--conf", type=float, default=0.25)
    p.add_argument("--save-annotated", action="store_true", help="Also save annotated images under runs/detect/predict/")
    p.add_argument("--out-jsonl", type=Path, default=Path("data/training/corpus_detections.jsonl"))
    args = p.parse_args()

    if not args.weights.is_file():
        raise SystemExit(f"weights not found: {args.weights}")
    if not args.folder.is_dir():
        raise SystemExit(f"folder not found: {args.folder}")

    from ultralytics import YOLO

    model = YOLO(str(args.weights))
    class_names = model.names
    print(f"loaded {args.weights} with {len(class_names)} classes: {list(class_names.values())}")

    images = sorted(p for p in args.folder.iterdir() if p.suffix.lower() in _IMAGE_EXTS)
    print(f"running on {len(images)} images at conf={args.conf}")

    per_class_total = Counter()
    per_photo_count = Counter()
    top_detections = []
    rows = []

    for img_path in images:
        results = model(str(img_path), conf=args.conf, save=args.save_annotated, verbose=False)
        r = results[0]
        det_classes = r.boxes.cls.tolist() if r.boxes is not None else []
        det_confs = r.boxes.conf.tolist() if r.boxes is not None else []
        det_boxes = r.boxes.xyxy.tolist() if r.boxes is not None else []

        photo_dets = []
        for cls_idx, conf, box in zip(det_classes, det_confs, det_boxes):
            cname = class_names[int(cls_idx)]
            photo_dets.append({"class": cname, "conf": round(float(conf), 3), "bbox": [round(float(x), 1) for x in box]})
            per_class_total[cname] += 1
            top_detections.append((float(conf), cname, img_path.name))
        per_photo_count[len(photo_dets)] += 1
        rows.append({"filename": img_path.name, "detections": photo_dets})

    args.out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with args.out_jsonl.open("w", encoding="utf-8") as fp:
        for row in rows:
            fp.write(json.dumps(row) + "\n")

    print("\n=== per-class detection counts ===")
    for name in class_names.values():
        print(f"  {name:35s}  {per_class_total.get(name, 0)}")

    print("\n=== detections-per-photo histogram ===")
    for n_dets, n_photos in sorted(per_photo_count.items()):
        print(f"  {n_dets} detection(s) in {n_photos} photo(s)")

    print("\n=== top 10 detections by confidence ===")
    top_detections.sort(reverse=True)
    for conf, cname, fname in top_detections[:10]:
        print(f"  {conf:.3f}  {cname:35s}  {fname}")

    print(f"\nfull per-photo detections written to: {args.out_jsonl}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
