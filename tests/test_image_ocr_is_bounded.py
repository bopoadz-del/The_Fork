"""A drone photo must not be handed to tesseract and YOLO at full resolution.

Live ingest on 9e6fe98, worker at 4096 MiB, run e6aa5eb2d22d. The oom_kill
counter went 3 -> 47 in seventy files, and every burst landed on the same
kind of file:

    oom_kill  0->1    DJI_20241119194400_0045_D.JPG
    oom_kill  2->10   DJI_20250303103135_0011_D.JPG
    oom_kill 18->23   DJI_20250303095624_0002_D.JPG
    oom_kill 29->38   DJI_20250303104052_0011_D.JPG
    oom_kill 38->47   20240605_064935.jpg

Twenty- to forty-eight-megapixel aerial photographs. The image branch runs the
OCR block AND the YOLO-World detector on each one at native size: a 40 MP JPEG
decodes to ~120 MB of RGB, the OCR preprocessor then converts, deskews and
LANCZOS-resamples it, tesseract is forked against the result, and torch decodes
the same file again for detection. The OCR preprocessor only ever UPSCALES
(``if min_dim < 1000``) -- there was no downscale cap on this path at all,
while the PDF page path has had one (``PDF_OCR_MAX_PIXELS``, 6 MP) for weeks.

Raising the task from 2048 to 4096 MiB made it worse (3 -> 47 kills, 4.7 ->
31.9 s/file): the process is single-threaded, so more room only meant a bigger
bitmap before the kernel stepped in. The Python RSS stayed near 1.1 GB against
a 3.3 GB cgroup, because the growth is in the forked tesseract and the decoded
bitmaps, not the interpreter heap.

The fix bounds the image ONCE, before either consumer sees it, at the same
6 MP the PDF path already uses. OCR accuracy on a site photo does not improve
past that; the PDF path was calibrated to it.
"""
from __future__ import annotations

import os

import pytest
from PIL import Image

from app.core import doc_index

BIG = (8000, 5000)      # 40 MP, the DJI range
SMALL = (1600, 1200)    # 1.9 MP, an ordinary phone photo


@pytest.fixture
def big_jpeg(tmp_path):
    p = tmp_path / "DJI_synthetic_0001_D.JPG"
    # Greyscale keeps the fixture cheap to build; the pixel count is the point.
    Image.new("L", BIG, 128).save(p, "JPEG", quality=30)
    return str(p)


@pytest.fixture
def small_jpeg(tmp_path):
    p = tmp_path / "site_photo.jpg"
    Image.new("L", SMALL, 128).save(p, "JPEG", quality=60)
    return str(p)


def _pixels(path):
    with Image.open(path) as im:
        return im.width * im.height


# ── the bound itself ───────────────────────────────────────────────────────

def test_a_forty_megapixel_photo_is_bounded(big_jpeg):
    with doc_index._bounded_image(big_jpeg) as bounded:
        assert bounded != big_jpeg, "a copy, not the original"
        assert _pixels(bounded) <= doc_index.image_ocr_max_pixels()
        assert _pixels(bounded) > doc_index.image_ocr_max_pixels() * 0.5, (
            "bounded, not thrown away -- OCR still needs the detail")


def test_a_small_photo_is_passed_through_untouched(small_jpeg):
    with doc_index._bounded_image(small_jpeg) as bounded:
        assert bounded == small_jpeg


def test_the_temp_copy_is_removed_afterwards(big_jpeg):
    with doc_index._bounded_image(big_jpeg) as bounded:
        assert os.path.exists(bounded)
    assert not os.path.exists(bounded)


def test_the_cap_is_tunable_and_defaults_to_the_pdf_paths_value(monkeypatch):
    monkeypatch.delenv("IMAGE_OCR_MAX_PIXELS", raising=False)
    assert doc_index.image_ocr_max_pixels() == 6_000_000
    monkeypatch.setenv("IMAGE_OCR_MAX_PIXELS", "2000000")
    assert doc_index.image_ocr_max_pixels() == 2_000_000


def test_a_nonsense_cap_falls_back(monkeypatch):
    monkeypatch.setenv("IMAGE_OCR_MAX_PIXELS", "lots")
    assert doc_index.image_ocr_max_pixels() == 6_000_000


def test_an_unreadable_file_yields_the_original_path(tmp_path):
    # Not an image at all: the bound must not turn a bad file into a crash.
    p = tmp_path / "notes.jpg"
    p.write_bytes(b"this is not a jpeg")
    with doc_index._bounded_image(str(p)) as bounded:
        assert bounded == str(p)


# ── both consumers receive the bounded copy ────────────────────────────────

def test_ocr_and_yolo_both_get_the_bounded_copy(monkeypatch, big_jpeg):
    # The bounded copy is removed when the branch finishes, so its size has
    # to be measured at the moment each consumer receives it -- reading it
    # afterwards would only prove the cleanup works.
    seen = {}

    def fake_ocr(path):
        seen["ocr"] = (path, _pixels(path)); return "", False

    def fake_yolo(path, name):
        seen["yolo"] = (path, _pixels(path)); return ""

    monkeypatch.setattr(doc_index, "_ocr_extract", fake_ocr)
    monkeypatch.setattr(doc_index, "_safety_world_extract", fake_yolo)
    doc_index._extract_with_meta(big_jpeg, os.path.basename(big_jpeg))
    assert "ocr" in seen and "yolo" in seen
    assert seen["ocr"][0] == seen["yolo"][0], "one bounded copy, decoded once, fed to both"
    assert seen["ocr"][0] != big_jpeg
    assert seen["ocr"][1] <= doc_index.image_ocr_max_pixels()
    assert not os.path.exists(seen["ocr"][0]), "and it is gone once the branch returns"


def test_a_small_photo_reaches_both_consumers_unchanged(monkeypatch, small_jpeg):
    seen = {}
    monkeypatch.setattr(doc_index, "_ocr_extract",
                        lambda path: (seen.setdefault("ocr", path), ("", False))[1])
    monkeypatch.setattr(doc_index, "_safety_world_extract",
                        lambda path, name: (seen.setdefault("yolo", path), "")[1])
    doc_index._extract_with_meta(small_jpeg, os.path.basename(small_jpeg))
    assert seen["ocr"] == small_jpeg and seen["yolo"] == small_jpeg
