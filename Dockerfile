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

# System deps - simplified for Render
RUN apt-get update || true && \
    apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    || true && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY . .

# Persistent data for ingest
VOLUME /app/data

EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
