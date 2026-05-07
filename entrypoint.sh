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

exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --no-access-log
