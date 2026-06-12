#!/bin/bash
# Start script for Cerebrum Blocks

# Use PORT from environment (Render sets this) or default to 8000
PORT=${PORT:-8000}

echo "🚀 Starting Cerebrum Blocks..."
echo "📍 Port: $PORT"
echo "📁 Data Directory: ${DATA_DIR:-/app/data}"

# Create data directory if it doesn't exist
mkdir -p ${DATA_DIR:-/app/data}

# Multi-worker when Redis backs shared session + rate-limit state
UVICORN_ARGS=(--host 0.0.0.0 --port "$PORT" --timeout-keep-alive 65)
if [ -n "$REDIS_URL" ]; then
  echo "🔀 REDIS_URL set — starting with 2 workers"
  UVICORN_ARGS+=(--workers 2)
else
  UVICORN_ARGS+=(--workers 1)
fi

exec uvicorn app.main:app "${UVICORN_ARGS[@]}"
