"""Re-download the 5 V1 source datasets that worked in V_final consolidation.

The V_final round's auto-delete cleaned them up. This script puts them back
quickly using the EXACT sources the V_final agent landed on - hardcoded
paths, no web search, no agent dispatch overhead.

Run before V2 (7-class) training to restore the 5 V1 classes' data; V2
agents handle the 2 new classes (fall_hazard_unprotected, bulging_concrete).

Usage:
    python scripts/redownload_v1_sources.py [--cap 250]
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import zipfile
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_REPO = Path(__file__).resolve().parents[1]
_EXTERNAL = _REPO / "data" / "training" / "external"


def _ensure_dir(p: Path) -> Path:
    (p / "images").mkdir(parents=True, exist_ok=True)
    (p / "labels").mkdir(parents=True, exist_ok=True)
    return p


def _write_manifest(class_dir: Path, source: str, source_url: str, total: int) -> None:
    images = list((class_dir / "images").iterdir())
    labels = list((class_dir / "labels").iterdir())
    size_mb = sum(p.stat().st_size for p in images + labels) / 1024 / 1024
    (class_dir / "manifest.json").write_text(json.dumps({
        "source": source,
        "source_url": source_url,
        "total_images": len(images),
        "total_labels": len(labels),
        "total_disk_mb": round(size_mb, 2),
    }, indent=2), encoding="utf-8")


def _coco_to_yolo_bbox(x: float, y: float, w: float, h: float, img_w: int, img_h: int) -> Tuple[float, float, float, float]:
    """COCO [x_min, y_min, w, h] (abs px) -> YOLO [x_center, y_center, w, h] normalized 0-1."""
    return (
        (x + w / 2) / img_w,
        (y + h / 2) / img_h,
        w / img_w,
        h / img_h,
    )


def _polygon_to_bbox(poly: List[float]) -> Tuple[float, float, float, float]:
    """[x1,y1,x2,y2,...] absolute px -> (xmin, ymin, xmax, ymax) absolute px."""
    xs = poly[0::2]
    ys = poly[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def download_no_hardhat(cap: int) -> dict:
    from huggingface_hub import snapshot_download
    class_dir = _ensure_dir(_EXTERNAL / "no_hardhat")
    src = Path(snapshot_download(repo_id="keremberke/hard-hat-detection", repo_type="dataset"))
    return _import_keremberke_class(src, class_dir, our_class_id=0, target_label="head", cap=cap,
                                     source="keremberke/hard-hat-detection",
                                     url="https://huggingface.co/datasets/keremberke/hard-hat-detection")


def download_no_high_vis_vest(cap: int) -> dict:
    from huggingface_hub import snapshot_download
    class_dir = _ensure_dir(_EXTERNAL / "no_high_vis_vest")
    src = Path(snapshot_download(repo_id="keremberke/construction-safety-object-detection", repo_type="dataset"))
    return _import_keremberke_class(src, class_dir, our_class_id=1, target_label="NO-Safety Vest", cap=cap,
                                     source="keremberke/construction-safety-object-detection",
                                     url="https://huggingface.co/datasets/keremberke/construction-safety-object-detection")


def _import_keremberke_class(src: Path, class_dir: Path, our_class_id: int, target_label: str, cap: int, source: str, url: str) -> dict:
    """Walk a keremberke COCO-format HF dataset; copy images whose annotations
    include target_label; rewrite labels to our YOLO class id."""
    from PIL import Image
    imported = 0
    for split_dir in src.glob("data/*"):
        if not split_dir.is_dir():
            continue
        ann_file = split_dir / "annotations" / "_annotations.coco.json"
        if not ann_file.is_file():
            for cand in split_dir.rglob("*.json"):
                if "annotations" in cand.name.lower() or "coco" in cand.name.lower():
                    ann_file = cand
                    break
        if not ann_file.is_file():
            continue
        ann = json.loads(ann_file.read_text(encoding="utf-8"))
        cat_map = {c["id"]: c["name"] for c in ann.get("categories", [])}
        target_id = next((cid for cid, name in cat_map.items() if name == target_label), None)
        if target_id is None:
            continue
        images_by_id = {img["id"]: img for img in ann["images"]}
        anns_by_img: dict[int, list] = {}
        for a in ann["annotations"]:
            anns_by_img.setdefault(a["image_id"], []).append(a)

        for img_id, img_meta in images_by_id.items():
            img_anns = anns_by_img.get(img_id, [])
            target_anns = [a for a in img_anns if a["category_id"] == target_id]
            if not target_anns:
                continue
            src_img = split_dir / img_meta["file_name"]
            if not src_img.is_file():
                continue
            try:
                with Image.open(src_img) as im:
                    img_w, img_h = im.size
            except Exception:
                continue
            dst_img = class_dir / "images" / src_img.name
            dst_lbl = class_dir / "labels" / (src_img.stem + ".txt")
            shutil.copy2(src_img, dst_img)
            lines = []
            for a in target_anns:
                bx, by, bw, bh = a["bbox"]
                xc, yc, w, h = _coco_to_yolo_bbox(bx, by, bw, bh, img_w, img_h)
                lines.append(f"{our_class_id} {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}")
            dst_lbl.write_text("\n".join(lines) + "\n", encoding="utf-8")
            imported += 1
            if imported >= cap:
                break
        if imported >= cap:
            break
    _write_manifest(class_dir, source, url, imported)
    return {"class": class_dir.name, "imported": imported}


def download_concrete_crack(cap: int) -> dict:
    import urllib.request
    class_dir = _ensure_dir(_EXTERNAL / "concrete_crack")
    url = "https://github.com/ultralytics/assets/releases/download/v0.0.0/crack-seg.zip"
    print(f"  fetching {url}")
    tmp_zip = class_dir / "_crack-seg.zip"
    urllib.request.urlretrieve(url, tmp_zip)
    print(f"  extracting")
    with zipfile.ZipFile(tmp_zip) as z:
        z.extractall(class_dir / "_extract")
    imported = 0
    from PIL import Image
    for img_dir in (class_dir / "_extract").rglob("images"):
        for img in sorted(img_dir.glob("*.jpg")):
            if imported >= cap:
                break
            lbl_src = img.parent.parent / "labels" / (img.stem + ".txt")
            if not lbl_src.is_file():
                continue
            try:
                with Image.open(img) as im:
                    img_w, img_h = im.size
            except Exception:
                continue
            new_lines = []
            for line in lbl_src.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) < 7:
                    continue
                coords = [float(x) for x in parts[1:]]
                abs_pts = []
                for i in range(0, len(coords), 2):
                    abs_pts.extend([coords[i] * img_w, coords[i + 1] * img_h])
                xmin, ymin, xmax, ymax = _polygon_to_bbox(abs_pts)
                xc = (xmin + xmax) / 2 / img_w
                yc = (ymin + ymax) / 2 / img_h
                w = (xmax - xmin) / img_w
                h = (ymax - ymin) / img_h
                new_lines.append(f"3 {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}")
            if not new_lines:
                continue
            shutil.copy2(img, class_dir / "images" / img.name)
            (class_dir / "labels" / (img.stem + ".txt")).write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            imported += 1
        if imported >= cap:
            break
    shutil.rmtree(class_dir / "_extract", ignore_errors=True)
    tmp_zip.unlink(missing_ok=True)
    _write_manifest(class_dir, "ultralytics crack-seg.zip", url, imported)
    return {"class": "concrete_crack", "imported": imported}


def download_concrete_honeycomb(cap: int) -> dict:
    """Pull HiC web subset images + annotations via GitHub raw URLs."""
    import urllib.request, urllib.parse
    class_dir = _ensure_dir(_EXTERNAL / "concrete_honeycomb")
    repo_api = "https://api.github.com/repos/jdkuhnke/HiC/contents/HiCIS/web"
    print(f"  listing {repo_api}")
    req = urllib.request.Request(repo_api, headers={"Accept": "application/vnd.github.v3+json"})
    with urllib.request.urlopen(req) as r:
        listing = json.loads(r.read())
    imported = 0
    from PIL import Image
    for item in listing:
        if imported >= cap:
            break
        if item["type"] != "file" or not item["name"].lower().endswith((".jpg", ".jpeg", ".png")):
            continue
        if " " in item["name"]:  # spaces break raw URL
            continue
        try:
            raw = item["download_url"]
            data = urllib.request.urlopen(raw, timeout=30).read()
            img_bytes = io.BytesIO(data)
            img = Image.open(img_bytes)
            img_w, img_h = img.size
            dst_img = class_dir / "images" / item["name"]
            dst_img.write_bytes(data)
            # HiC web subset is image-level (whole image is honeycomb); write a single bbox covering the image
            lbl = class_dir / "labels" / (Path(item["name"]).stem + ".txt")
            lbl.write_text(f"4 0.5 0.5 1.0 1.0\n", encoding="utf-8")
            imported += 1
        except Exception:
            continue
    _write_manifest(class_dir, "jdkuhnke/HiC HiCIS/web", "https://github.com/jdkuhnke/HiC", imported)
    return {"class": "concrete_honeycomb", "imported": imported}


def download_rebar_correct_inspection(cap: int) -> dict:
    from huggingface_hub import snapshot_download
    class_dir = _ensure_dir(_EXTERNAL / "rebar_correct_inspection")
    src = Path(snapshot_download(repo_id="tsrobcvai/ROI-1555", repo_type="dataset"))
    imported = 0
    from PIL import Image
    for json_file in src.rglob("*.json"):
        if imported >= cap:
            break
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if "shapes" not in data:
            continue
        img_path = json_file.with_suffix("")
        for ext in (".jpg", ".jpeg", ".png"):
            cand = img_path.with_suffix(ext)
            if cand.is_file():
                img_path = cand
                break
        else:
            continue
        try:
            with Image.open(img_path) as im:
                img_w, img_h = im.size
        except Exception:
            continue
        lines = []
        for shape in data["shapes"]:
            pts = [p for xy in shape["points"] for p in xy]
            if len(pts) < 4:
                continue
            xmin, ymin, xmax, ymax = _polygon_to_bbox(pts)
            xc = (xmin + xmax) / 2 / img_w
            yc = (ymin + ymax) / 2 / img_h
            w = (xmax - xmin) / img_w
            h = (ymax - ymin) / img_h
            lines.append(f"5 {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}")
        if not lines:
            continue
        shutil.copy2(img_path, class_dir / "images" / img_path.name)
        (class_dir / "labels" / (img_path.stem + ".txt")).write_text("\n".join(lines) + "\n", encoding="utf-8")
        imported += 1
    _write_manifest(class_dir, "tsrobcvai/ROI-1555", "https://huggingface.co/datasets/tsrobcvai/ROI-1555", imported)
    return {"class": "rebar_correct_inspection", "imported": imported}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cap", type=int, default=250, help="Max images per class")
    p.add_argument("--only", nargs="*", default=None, help="Only download specific classes")
    args = p.parse_args()

    funcs = [
        ("no_hardhat", download_no_hardhat),
        ("no_high_vis_vest", download_no_high_vis_vest),
        ("concrete_crack", download_concrete_crack),
        ("concrete_honeycomb", download_concrete_honeycomb),
        ("rebar_correct_inspection", download_rebar_correct_inspection),
    ]

    results = []
    for name, fn in funcs:
        if args.only and name not in args.only:
            continue
        print(f"\n=== {name} ===")
        try:
            result = fn(args.cap)
            print(f"  {result['imported']} images imported")
            results.append(result)
        except Exception as exc:
            print(f"  FAILED: {type(exc).__name__}: {exc}")
            results.append({"class": name, "imported": 0, "error": str(exc)})

    print("\n=== summary ===")
    for r in results:
        print(f"  {r['class']:35s}  {r.get('imported', 0)} imgs" + (f"  ERROR: {r['error']}" if r.get("error") else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
