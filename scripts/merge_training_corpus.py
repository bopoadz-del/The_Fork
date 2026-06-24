"""Merge per-class external datasets + operator-labelled corpus into one YOLO training set.

Inputs:
- ``data/training/external/<class_name>/images/`` + ``data/training/external/<class_name>/labels/``
  per-class YOLO format (downloaded by data-acquisition agents)
- ``data/training/external/<class_name>/manifest.json`` (provenance)
- Optional: ``data/training/labels_final/`` (operator's own Label Studio export, if present)

Output:
- ``data/training/labels_final/images/{train,val}/*.jpg``
- ``data/training/labels_final/labels/{train,val}/*.txt`` (YOLO format)
- ``data/training/labels_final/data.yaml`` (consumed by scripts/train_safety_qaqc.py)
- ``data/training/labels_final/sources.json`` (provenance manifest of merged sources)

Class IDs in the merged dataset are stable per ``app/blocks/safety_classes.json`` — only
``active=True`` classes appear; the YOLO model sees them renumbered 0..N-1 via a class map
saved alongside the weights.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.blocks.safety_classes import get_active_classes  # noqa: E402

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_SEED = 42
_VAL_FRACTION = 0.2


def _discover_external(external_dir: Path) -> Dict[str, Path]:
    """Return {class_name: class_dir} for every active class with downloaded images."""
    active = {c.name: c for c in get_active_classes()}
    found = {}
    for child in external_dir.iterdir():
        if not child.is_dir():
            continue
        if child.name not in active:
            continue
        images_dir = child / "images"
        labels_dir = child / "labels"
        if images_dir.is_dir() and labels_dir.is_dir():
            found[child.name] = child
    return found


def _list_pairs(class_dir: Path) -> List[Tuple[Path, Path]]:
    """Return [(image_path, label_path), ...] for a class. Skips images with no label."""
    images_dir = class_dir / "images"
    labels_dir = class_dir / "labels"
    pairs: List[Tuple[Path, Path]] = []
    for img in sorted(images_dir.iterdir()):
        if img.suffix.lower() not in _IMAGE_EXTS:
            continue
        lbl = labels_dir / (img.stem + ".txt")
        if lbl.is_file():
            pairs.append((img, lbl))
    return pairs


def merge(external_dir: Path, out_dir: Path) -> Dict:
    active = list(get_active_classes())
    active_by_id = {c.id: c for c in active}
    yolo_id_for = {c.id: i for i, c in enumerate(active)}
    yolo_names = [c.name for c in active]

    classes_found = _discover_external(external_dir)
    if not classes_found:
        raise SystemExit(f"no active-class folders with images+labels under {external_dir}")

    for split in ("train", "val"):
        (out_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (out_dir / "labels" / split).mkdir(parents=True, exist_ok=True)

    rng = random.Random(_SEED)
    per_class_counts: Dict[str, Dict[str, int]] = {}
    sources_manifest: List[Dict] = []

    for class_name, class_dir in sorted(classes_found.items()):
        registry_entry = next(c for c in active if c.name == class_name)
        our_yolo_id = yolo_id_for[registry_entry.id]

        pairs = _list_pairs(class_dir)
        if not pairs:
            per_class_counts[class_name] = {"train": 0, "val": 0, "total": 0}
            continue

        rng.shuffle(pairs)
        split_at = max(1, int(len(pairs) * (1 - _VAL_FRACTION)))
        train_pairs = pairs[:split_at]
        val_pairs = pairs[split_at:]

        for split, split_pairs in (("train", train_pairs), ("val", val_pairs)):
            for img, lbl in split_pairs:
                dst_img = out_dir / "images" / split / f"{class_name}__{img.name}"
                dst_lbl = out_dir / "labels" / split / f"{class_name}__{img.stem}.txt"
                shutil.copy2(img, dst_img)
                _rewrite_label_with_class_id(lbl, dst_lbl, our_yolo_id)

        per_class_counts[class_name] = {
            "train": len(train_pairs),
            "val": len(val_pairs),
            "total": len(pairs),
        }

        manifest_path = class_dir / "manifest.json"
        if manifest_path.is_file():
            try:
                sources_manifest.append({
                    "class": class_name,
                    **json.loads(manifest_path.read_text(encoding="utf-8")),
                })
            except json.JSONDecodeError:
                sources_manifest.append({"class": class_name, "manifest_error": True})

    data_yaml = out_dir / "data.yaml"
    data_yaml.write_text(
        f"path: {out_dir.resolve().as_posix()}\n"
        "train: images/train\n"
        "val: images/val\n"
        f"names: {yolo_names}\n",
        encoding="utf-8",
    )

    sources_json = out_dir / "sources.json"
    sources_json.write_text(json.dumps({
        "active_classes": [{"id": c.id, "name": c.name, "yolo_idx": yolo_id_for[c.id]} for c in active],
        "per_class_counts": per_class_counts,
        "sources": sources_manifest,
        "split_seed": _SEED,
        "val_fraction": _VAL_FRACTION,
    }, indent=2), encoding="utf-8")

    return {
        "data_yaml": str(data_yaml),
        "sources_json": str(sources_json),
        "per_class_counts": per_class_counts,
        "active_classes": yolo_names,
    }


def _rewrite_label_with_class_id(src: Path, dst: Path, new_class_id: int) -> None:
    """Read a single-class YOLO label file and rewrite each row's class column.

    Source labels in ``data/training/external/<class>/labels/`` are produced by the
    download agents; each file is assumed to contain rows for ONE class (the
    folder's class). We rewrite the class column to our YOLO-side index so the
    merged dataset has a single, consistent class-id namespace.
    """
    out_lines: List[str] = []
    for line in src.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split()
        if len(parts) < 5:
            continue
        out_lines.append(f"{new_class_id} {' '.join(parts[1:])}")
    dst.write_text("\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--external-dir", type=Path, default=Path("data/training/external"))
    p.add_argument("--out-dir", type=Path, default=Path("data/training/labels_final"))
    args = p.parse_args()
    result = merge(args.external_dir, args.out_dir)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
