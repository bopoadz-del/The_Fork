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

FROM python:3.11-slim
WORKDIR /app

# Runtime system libs (OpenGL/glib for image processing; curl for healthcheck).
# No "|| true" — a missing dependency must fail the build, not surface later
# as a runtime crash on first import.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY . .

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
