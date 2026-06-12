#!/bin/bash
echo "🚀 Cerebrum Blocks starting on $(uname -m) architecture"

# Hardware detection (future-proof for local OCR/voice/GPU blocks)
if command -v nvidia-smi &> /dev/null; then
    export HARDWARE="gpu"
    echo "✅ NVIDIA GPU detected"
elif [ -d "/proc/device-tree" ] && grep -q "nvidia" /proc/device-tree/model 2>/dev/null; then
    export HARDWARE="jetson"
    echo "✅ NVIDIA Jetson detected"
else
    export HARDWARE="cpu"
    echo "ℹ️  Running on CPU"
fi

# Render / cloud compatibility ($PORT)
if [ -n "$PORT" ]; then
    PORT=${PORT}
else
    PORT=8000
fi

echo "📡 Listening on 0.0.0.0:$PORT (HARDWARE=$HARDWARE)"

command -v ODAFileConverter >/dev/null 2>&1 || echo "⚠️  ODAFileConverter not on PATH — drawing_qto will reject .dwg uploads with a guidance error"

if [ -n "${DATABASE_URL:-}" ] && [[ "${DATABASE_URL}" == postgresql* ]]; then
  echo "🗄️  DATABASE_URL set — running alembic upgrade head"
  if ! python -m alembic upgrade head; then
    echo "❌ alembic upgrade failed — refusing to start with a stale schema"
    exit 1
  fi
fi

UVICORN_ARGS=(--host 0.0.0.0 --port "$PORT" --no-access-log --timeout-keep-alive 65)
# REDIS_URL backs shared sessions/rate limits; worker count is separate.
# Render starter (512Mi) OOMs with 2 workers + model2vec — default 1.
WORKERS="${UVICORN_WORKERS:-1}"
if [ -n "$REDIS_URL" ]; then
  echo "🔀 REDIS_URL set — Redis session/rate-limit backend active"
fi
echo "👷 uvicorn workers: ${WORKERS}"
UVICORN_ARGS+=(--workers "$WORKERS")

exec uvicorn app.main:app "${UVICORN_ARGS[@]}"
