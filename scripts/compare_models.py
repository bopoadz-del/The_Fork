"""Evaluate two YOLO weight files on the same dataset YAML; print a side-by-side per-class comparison.

Useful for honestly comparing successive rounds (e.g. V_final vs V2) on a held-out test set.

Usage:
    python scripts/compare_models.py --data data/training/test/test.yaml --weights data/models/safety_qaqc_v1_r3.pt data/models/safety_qaqc_v1_r4.pt
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def evaluate(weights_path: Path, data_yaml: Path) -> dict:
    from ultralytics import YOLO
    model = YOLO(str(weights_path))
    metrics = model.val(data=str(data_yaml), split="val", verbose=False, save=False, plots=False)
    return {
        "weights": str(weights_path),
        "weights_size_mb": round(weights_path.stat().st_size / 1024 / 1024, 1),
        "yolo_names": dict(metrics.names),
        "overall": {
            "precision": float(metrics.box.mp),
            "recall": float(metrics.box.mr),
            "mAP_0.5": float(metrics.box.map50),
            "mAP_0.5:0.95": float(metrics.box.map),
        },
        "per_class": {
            metrics.names[i]: {
                "precision": float(metrics.box.p[i]),
                "recall": float(metrics.box.r[i]),
                "mAP_0.5": float(metrics.box.maps[i]),
            }
            for i in range(len(metrics.names))
        },
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", type=Path, required=True, help="Path to a YOLO dataset YAML")
    p.add_argument("--weights", type=Path, nargs="+", required=True, help="One or more .pt files to compare")
    args = p.parse_args()

    if not args.data.is_file():
        raise SystemExit(f"data yaml not found: {args.data}")

    results = []
    for w in args.weights:
        if not w.is_file():
            print(f"skipping {w} (file not found)")
            continue
        print(f"\n=== evaluating {w.name} ===")
        results.append(evaluate(w, args.data))

    if not results:
        return 1

    print(f"\n=== side-by-side per-class (dataset: {args.data}) ===\n")
    all_classes = sorted({c for r in results for c in r["per_class"]})
    headers = ["class"] + [Path(r["weights"]).stem for r in results]
    print("  " + "  ".join(f"{h:32s}" for h in headers))
    print("  " + "  ".join("-" * 32 for _ in headers))
    for cls in all_classes:
        cols = [cls]
        for r in results:
            entry = r["per_class"].get(cls)
            if entry is None:
                cols.append("--")
            else:
                cols.append(f"P={entry['precision']:.2f} R={entry['recall']:.2f} mAP50={entry['mAP_0.5']:.3f}")
        print("  " + "  ".join(f"{c:32s}" for c in cols))
    print("\n  " + "  ".join(f"{h:32s}" for h in (["overall"] + [Path(r['weights']).stem for r in results])))
    cols = ["overall"]
    for r in results:
        cols.append(f"P={r['overall']['precision']:.2f} R={r['overall']['recall']:.2f} mAP50={r['overall']['mAP_0.5']:.3f}")
    print("  " + "  ".join(f"{c:32s}" for c in cols))

    out_json = args.data.parent / f"compare_eval_{'_vs_'.join(Path(r['weights']).stem for r in results)}.json"
    out_json.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nfull results written to {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
