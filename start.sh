#!/bin/bash
# Start script for Cerebrum Blocks

# Use PORT from environment (Render sets this) or default to 8000
PORT=${PORT:-8000}

echo "🚀 Starting Cerebrum Blocks..."
echo "📍 Port: $PORT"
echo "📁 Data Directory: ${DATA_DIR:-/app/data}"

# Create data directory if it doesn't exist
mkdir -p ${DATA_DIR:-/app/data}

# REDIS_URL backs shared session + rate-limit state; set UVICORN_WORKERS=2
# on hosts with enough RAM (Render starter 512Mi should stay at 1).
UVICORN_ARGS=(--host 0.0.0.0 --port "$PORT" --timeout-keep-alive 65)
WORKERS="${UVICORN_WORKERS:-1}"
if [ -n "$REDIS_URL" ]; then
  echo "🔀 REDIS_URL set — Redis session/rate-limit backend active"
fi
echo "👷 uvicorn workers: ${WORKERS}"
UVICORN_ARGS+=(--workers "$WORKERS")

exec uvicorn app.main:app "${UVICORN_ARGS[@]}"
