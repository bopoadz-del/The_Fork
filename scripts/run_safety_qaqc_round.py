"""Per-round driver: train + save weights + delete training data.

Each "round" trains YOLO on whatever active classes have data in
``data/training/external/<class>/``, saves the resulting weights to
``data/models/safety_qaqc_v1_r{N}.pt`` + classmap, then deletes
``data/training/external/*`` and ``data/training/labels_final/`` so disk
stays under the operator's 1 GB cap before the next batch is fetched.

If ``--resume-from`` is passed, training initializes from that .pt
(transfer-learning from the prior round's best). Otherwise it starts
from the COCO-pretrained ``yolov8n.pt`` baseline.

Usage:
    python scripts/run_safety_qaqc_round.py --round 1 [--epochs 30] [--resume-from data/models/safety_qaqc_v1_r0.pt]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def _run(cmd: list) -> int:
    print(f"$ {' '.join(str(c) for c in cmd)}", flush=True)
    return subprocess.call(cmd)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--round", type=int, required=True)
    p.add_argument("--epochs", type=int, default=30)
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--resume-from", type=Path, default=None,
                   help="Prior round's .pt to transfer-learn from")
    p.add_argument("--keep-data", action="store_true",
                   help="Skip the post-train delete (debug only)")
    p.add_argument("--test-per-class", type=int, default=0,
                   help="Hold out N images per class as test set; eval on it post-training")
    args = p.parse_args()

    repo = Path(__file__).resolve().parents[1]
    external = repo / "data" / "training" / "external"
    labels_final = repo / "data" / "training" / "labels_final"
    test_set_dir = repo / "data" / "training" / "test"
    models_dir = repo / "data" / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    merge_cmd = [
        sys.executable,
        str(repo / "scripts" / "merge_training_corpus.py"),
        "--external-dir", str(external),
        "--out-dir", str(labels_final),
    ]
    if args.test_per_class > 0:
        merge_cmd.extend(["--test-per-class", str(args.test_per_class), "--test-dir", str(test_set_dir)])
    if _run(merge_cmd) != 0:
        print("merge failed", file=sys.stderr)
        return 1

    data_yaml = labels_final / "data.yaml"
    if not data_yaml.is_file():
        print(f"no data.yaml at {data_yaml}", file=sys.stderr)
        return 1

    base_model = str(args.resume_from) if args.resume_from else "yolov8n.pt"
    print(f"\n=== Round {args.round}: training from {base_model} ===\n")
    try:
        from ultralytics import YOLO
    except ImportError:
        print("ultralytics not installed", file=sys.stderr)
        return 1

    model = YOLO(base_model)
    results = model.train(
        data=str(data_yaml),
        epochs=args.epochs,
        imgsz=args.imgsz,
        seed=42,
        verbose=True,
        device="cpu",
        project=str(repo / "runs" / "detect"),
        name=f"round{args.round}",
        exist_ok=True,
    )

    best = repo / "runs" / "detect" / f"round{args.round}" / "weights" / "best.pt"
    dst = models_dir / f"safety_qaqc_v1_r{args.round}.pt"
    if best.is_file():
        shutil.copy2(best, dst)
        print(f"\nweights saved to {dst}")
    else:
        print(f"WARNING: expected best.pt not found at {best}", file=sys.stderr)
        return 1

    if args.test_per_class > 0:
        test_yaml = test_set_dir / "test.yaml"
        if test_yaml.is_file():
            print(f"\n=== Round {args.round}: held-out test eval on {test_yaml} ===\n")
            eval_model = YOLO(str(dst))
            test_metrics = eval_model.val(data=str(test_yaml), split="val", project=str(repo / "runs" / "detect"),
                                          name=f"round{args.round}_test", exist_ok=True, verbose=True)
            test_summary = {
                "mAP_0.5": float(test_metrics.box.map50),
                "mAP_0.5:0.95": float(test_metrics.box.map),
                "per_class": {
                    name: {"precision": float(test_metrics.box.p[i]),
                           "recall": float(test_metrics.box.r[i]),
                           "mAP_0.5": float(test_metrics.box.maps[i])}
                    for i, name in test_metrics.names.items()
                },
            }
            test_summary_path = repo / "data" / "training" / f"test_eval_r{args.round}.json"
            test_summary_path.parent.mkdir(parents=True, exist_ok=True)
            import json as _json
            test_summary_path.write_text(_json.dumps(test_summary, indent=2), encoding="utf-8")
            print(f"held-out test eval written to {test_summary_path}")
        else:
            print(f"no test.yaml at {test_yaml} (test_per_class was set but merge didn't write one)", file=sys.stderr)

    if not args.keep_data:
        for child in external.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
                child.mkdir(exist_ok=True)
        if labels_final.is_dir():
            shutil.rmtree(labels_final, ignore_errors=True)
        if test_set_dir.is_dir():
            shutil.rmtree(test_set_dir, ignore_errors=True)
        print("training + test data deleted; disk freed")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
