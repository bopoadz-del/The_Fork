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

echo "=== Build complete ==="
