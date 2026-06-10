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

# Runtime system libs (OpenGL/glib for image processing; curl for healthcheck;
# tesseract + Arabic language pack so Arabic BOQ pages OCR correctly per
# FOLLOW-UP #93 — without ara, PyMuPDF's CMAP-less Arabic text becomes
# mojibake and downstream chunks lose ground truth for rate-points).
# No "|| true" — a missing dependency must fail the build, not surface later
# as a runtime crash on first import.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    curl \
    tesseract-ocr \
    tesseract-ocr-ara \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# ODA File Converter — required by app.blocks.drawing_qto for DWG → DXF.
# Override at build time if the upstream version changes:
#   docker build --build-arg ODA_URL=https://.../ODAFileConverter_QT6_lnxX64_*.deb .
ARG ODA_URL="https://www.opendesign.com/guestfiles/get?filename=ODAFileConverter_QT6_lnxX64_8.3dll_27.1.deb"
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxext6 libsm6 libxrender1 libice6 libxi6 \
        libxcomposite1 libxcursor1 libxdamage1 libxfixes3 libxrandr2 \
        libxtst6 libnss3 \
    && curl -fSL -A "Mozilla/5.0" -o /tmp/oda.deb "${ODA_URL}" \
    && apt-get install -y --no-install-recommends /tmp/oda.deb \
    && rm /tmp/oda.deb \
    && rm -rf /var/lib/apt/lists/*

ENV QT_QPA_PLATFORM=offscreen

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY . .
# Replace the (gitignored) frontend/dist with the freshly built one.
COPY --from=frontend /frontend/dist /app/frontend/dist

# Run as an unprivileged user. /app/data (the persistent volume) and the app
# tree must be owned by it so the process can write its DBs and uploads.
RUN useradd --create-home --uid 10001 appuser \
    && chmod +x /app/entrypoint.sh \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

# Persistent data for ingest
VOLUME /app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
