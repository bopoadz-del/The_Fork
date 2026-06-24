#!/usr/bin/env bash
set -e

echo "=== Installing system packages ==="
apt-get update -qq
apt-get install -y -qq \
  tesseract-ocr \
  tesseract-ocr-ara \
  libgl1 \
  libglib2.0-0

echo "=== Installing Python dependencies ==="
pip install --upgrade pip
pip install -r requirements.txt

echo "=== Installing CPU torch + ultralytics for V2 safety detector ==="
# CPU wheels only — Render starter has no GPU. ~320 MB additional.
# SAFETY_DETECTOR_WEIGHTS env var (set in Render dashboard) points the
# block at the committed data/models/safety_qaqc_v1_r4.pt; ultralytics
# loads that on first detection call, no remote download.
pip install --extra-index-url https://download.pytorch.org/whl/cpu \
  "torch==2.5.1" \
  "torchvision==0.20.1" \
  "ultralytics==8.4.75" \
  "opencv-python-headless==4.13.0.92"

echo "=== Build complete ==="
