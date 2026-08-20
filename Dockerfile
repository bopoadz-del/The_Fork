# Multi-stage build - slim, fast, multi-platform
FROM python:3.11-slim AS builder
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    gfortran \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Strip test-only tooling from the RUNTIME image.
#
# requirements.txt is the single pip-compile lock and pins pytest + friends,
# so without this the production container ships a test framework it never
# invokes -- needless surface (it is what keeps the pytest tmpdir advisory
# attached to the production manifest) and needless image weight.
#
# Safe to remove here because:
#   * nothing under app/ imports pytest at runtime (verified 2026-08-02:
#     zero `import pytest` / `from pytest` outside tests/),
#   * CI does NOT rely on requirements.txt for these -- .github/workflows/
#     test.yml installs pytest/pytest-asyncio/pytest-cov/pytest-timeout
#     explicitly before running the suite,
#   * this runs only in the image build, so `pip install -r requirements.txt`
#     on a dev machine is unchanged.
#
# diff-cover is intentionally in the list: it is the per-PR coverage gate,
# a CI tool with no runtime role.
RUN pip uninstall -y \
        pytest pytest-asyncio pytest-cov pytest-json-report diff-cover \
    || true

# Safety Observation AI v2 detector dependencies -- CPU wheels only.
# SAFETY_WORLD_WEIGHTS env var on Render points at the committed
# data/models/safety_world_v2.onnx -- a YOLO-Worldv2-s checkpoint with
# its prompt vocabulary reparameterized into the classifier head at
# bake time, then exported to ONNX (see scripts/bake_world_model.py +
# scripts/export_to_onnx steps). CLIP is NOT a runtime dep: the text
# vectors are baked into the .onnx; ultralytics' YOLO() loader treats
# it as a regular detector backed by onnxruntime.
RUN pip install --no-cache-dir \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        "torch==2.5.1" \
        "torchvision==0.20.1" \
        "ultralytics==8.4.75" \
        "onnxruntime==1.27.0"
# Replace whatever opencv ultralytics pulled (opencv-python 4.13 has known
# cv2.imdecode failures under numpy 2.x ABI) with the older, ABI-safe
# headless 4.10.0.84. uninstall both packages first because pip treats
# opencv-python and opencv-python-headless as separate identities, so
# straight install of headless leaves the broken opencv-python on disk.
RUN pip uninstall -y opencv-python opencv-python-headless \
    && pip install --no-cache-dir "opencv-python-headless==4.10.0.84"

# Sentence-transformers for the RAG embedder. Installed AFTER the CPU torch
# wheels above so pip sees torch is already satisfied and does not pull the
# CUDA variant. BGE-small and other dense sentence-transformers models need
# this; model2vec alone cannot load them.
RUN pip install --no-cache-dir "sentence-transformers==5.5.1"

# ── Bake the RAG embedder weights into the image ────────────────────────────
#
# WHY: the weights were never in the image. Only the LIBRARIES were installed,
# so the first embed call resolved the model name against huggingface.co at
# RUNTIME via snapshot_download. Render containers are ephemeral and no
# HF_HOME/persistent cache was configured, which made every deploy, restart and
# scale event re-download the model — a live third-party host sitting in the
# boot path of the retrieval stack.
#
# The failure mode was silent, which is what made it dangerous: doc_index wraps
# its RAG hook in try/except, so a failed download meant a document was stored,
# registered and listed while being indexed with ZERO chunks. No failed upload,
# no error in the UI — just a document that is permanently unsearchable. A live
# 403 from the Hub reproduced exactly that.
#
# Baking it here makes the image self-contained: the model is present before
# the container ever starts, and HF_HUB_OFFLINE in the runtime stage means a
# network fetch cannot be attempted at all.
#
# The model name is an ARG so it stays single-sourced, but it MUST match what
# the running corpus was embedded with. Vectors carry an embedding identity
# ({model, dim, normalized}) and VectorStore._verify_embedding_identity refuses
# a namespace whose stamp disagrees — so changing this value without
# re-embedding the corpus takes retrieval down. It is pinned to the value the
# code already defaulted to (embeddings.DEFAULT_MODEL2VEC), which is what
# production has been running with RAG_EMBEDDING_MODEL unset.
ARG RAG_EMBEDDING_MODEL="minishlab/potion-base-8M"
ENV HF_HOME=/opt/hf
# The script mirrors Embedder.__init__'s backend selection (sentence-
# transformers first, model2vec second) so the cache is populated by the SAME
# loader that will read it at runtime, and verifies the weights load offline
# before the layer is committed. No "|| true": a model that cannot be fetched
# must fail the BUILD, loudly, rather than become a silent empty index in
# production.
COPY scripts/prefetch_embedder.py /tmp/prefetch_embedder.py
RUN python /tmp/prefetch_embedder.py "${RAG_EMBEDDING_MODEL}" && rm /tmp/prefetch_embedder.py

# Frontend stage: build the React SPA. VITE_API_BASE='' makes the app talk to
# the same origin it was served from, so a single Render service is enough.
FROM node:20-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ENV VITE_API_BASE=""
RUN npm run build

FROM python:3.11-slim
WORKDIR /app

# Ultralytics settings dir — home is not writable as the non-root app user.
ENV YOLO_CONFIG_DIR=/tmp/Ultralytics

# Runtime system libs (OpenGL/glib for image processing; curl for healthcheck;
# tesseract + Arabic language pack so Arabic BOQ pages OCR correctly per
# FOLLOW-UP #93 — without ara, PyMuPDF's CMAP-less Arabic text becomes
# mojibake and downstream chunks lose ground truth for rate-points;
# ffmpeg so pydub can decode WebM/MP3/m4a/Ogg uploads from the browser
# push-to-talk path — without it, voice 2.2's STT returns an
# "install ffmpeg" error on every non-WAV recording).
# No "|| true" — a missing dependency must fail the build, not surface later
# as a runtime crash on first import.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    curl \
    ffmpeg \
    tesseract-ocr \
    tesseract-ocr-ara \
    tesseract-ocr-eng \
    antiword \
    catdoc \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*
# antiword/catdoc: app.core.doc_index._extract_doc converts legacy binary .doc
# by shelling out to antiword, then catdoc. Neither was in the image, and its
# remaining fallbacks cannot apply here -- textract is not a declared
# dependency and win32com is Windows-only -- so shutil.which() returned None
# for both, the converter list came out EMPTY, and EVERY .doc extracted to ""
# and indexed as ZERO_CHUNK. Confirmed live 2026-08-20 on a freshly uploaded
# .doc whose file was definitely present.
# nodejs+npm: app.blocks.mcp_consumer spawns external MCP servers via
# `npx -y @modelcontextprotocol/server-<name>` (F35 -- without node in the
# RUNTIME stage the external-mcp agent was a ghost; node:20-slim above is
# only the frontend BUILD stage and never reaches this image).

# ODA File Converter — required by app.blocks.drawing_qto for DWG → DXF.
# Override at build time if the upstream version changes:
#   docker build --build-arg ODA_URL=https://.../ODAFileConverter_QT6_lnxX64_*.deb .
ARG ODA_URL="https://www.opendesign.com/guestfiles/get?filename=ODAFileConverter_QT6_lnxX64_8.3dll_27.1.deb"
# xvfb: the ODA QT6 bundle ships ONLY the xcb platform plugin (no
# offscreen), so headless conversion needs a virtual X display --
# drawing_qto wraps the converter in `xvfb-run -a` when available.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxext6 libsm6 libxrender1 libice6 libxi6 \
        libxcomposite1 libxcursor1 libxdamage1 libxfixes3 libxrandr2 \
        libxtst6 libnss3 xvfb xauth libxcb-cursor0 libxkbcommon-x11-0 \
        libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 \
        libxcb-shape0 \
    && curl -fSL -A "Mozilla/5.0" -o /tmp/oda.deb "${ODA_URL}" \
    && apt-get install -y --no-install-recommends /tmp/oda.deb \
    && rm /tmp/oda.deb \
    && rm -rf /var/lib/apt/lists/*

ENV QT_QPA_PLATFORM=offscreen

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# ── RAG embedder weights, baked (see the builder stage for the full rationale)
#
# HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE are the load-bearing half: without them a
# cache MISS silently falls back to a network fetch, which is the behaviour
# being removed. With them, a miss raises at load time — a loud failure instead
# of documents silently indexed with zero chunks.
#
# HF_HOME lives OUTSIDE /app on purpose: /app/data is a mounted volume at
# runtime and the mount overlay would hide anything baked underneath it (the
# same trap the safety detector weights already work around by copying to
# /app/models).
COPY --from=builder /opt/hf /opt/hf
ENV HF_HOME=/opt/hf \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

COPY . .
# Replace the (gitignored) frontend/dist with the freshly built one.
COPY --from=frontend /frontend/dist /app/frontend/dist

# Copy detector weights OUT of /app/data (which is a volume mount at
# runtime -- the volume overlay hides the image's content) to a stable,
# non-volume location. SAFETY_WORLD_WEIGHTS on Render points here.
RUN mkdir -p /app/models \
    && cp /app/data/models/safety_world_v2.onnx /app/models/safety_world_v2.onnx

# Run as an unprivileged user. /app/data (the persistent volume) and the app
# tree must be owned by it so the process can write its DBs and uploads.
# /opt/hf is chowned too: huggingface_hub writes lock files beside the cache
# even on a pure read, so a root-owned cache would fail for the app user.
RUN useradd --create-home --uid 10001 appuser \
    && chmod +x /app/entrypoint.sh \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app /opt/hf
USER appuser

# Persistent data for ingest
VOLUME /app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/livez || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
