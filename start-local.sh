#!/usr/bin/env bash
# Local launcher - boots Cerebrum Blocks API + React dashboard with no Render dependency.
# Usage:
#   ./start-local.sh            # build dashboard if missing, then start backend on :8000
#   ./start-local.sh --rebuild  # force a fresh dashboard build
#   ./start-local.sh --dev      # also run vite dev server on :5173 with HMR
set -euo pipefail

cd "$(dirname "$0")"

REBUILD=0
DEV=0
for arg in "$@"; do
  case "$arg" in
    --rebuild) REBUILD=1 ;;
    --dev)     DEV=1 ;;
    -h|--help)
      grep '^# ' "$0" | sed 's/^# //'; exit 0 ;;
  esac
done

PORT="${PORT:-8000}"
export DATA_DIR="${DATA_DIR:-$PWD/data}"
export ENV="${ENV:-development}"
mkdir -p "$DATA_DIR"

# Load .env if present (so DEEPSEEK_API_KEY etc. flow through)
if [ -f .env ]; then
  set -a; . ./.env; set +a
fi

# Build dashboard if dist is missing or rebuild requested
if [ ! -f frontend/dist/index.html ] || [ "$REBUILD" = "1" ]; then
  echo "Building dashboard..."
  pushd frontend >/dev/null
  [ -d node_modules ] || npm install
  VITE_API_URL="${VITE_API_URL:-http://localhost:$PORT}" \
    VITE_API_KEY="${VITE_API_KEY:-cb_dev_key}" \
    npm run build
  popd >/dev/null
fi

# Optional: vite dev server with HMR (proxies /v1, /chat, ... to :8000)
if [ "$DEV" = "1" ]; then
  echo "Starting vite dev server on :5173 (HMR)..."
  pushd frontend >/dev/null
  VITE_API_URL="http://localhost:$PORT" \
    VITE_API_KEY="${VITE_API_KEY:-cb_dev_key}" \
    VITE_BASE="/" \
    npm run dev -- --host 0.0.0.0 &
  VITE_PID=$!
  popd >/dev/null
  trap 'kill $VITE_PID 2>/dev/null || true' EXIT
fi

echo
echo "Cerebrum Blocks - local mode"
echo "  Backend:   http://localhost:$PORT"
echo "  API docs:  http://localhost:$PORT/docs"
echo "  Dashboard: http://localhost:$PORT/dashboard/"
[ "$DEV" = "1" ] && echo "  Vite dev:  http://localhost:5173/  (HMR)"
echo

exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
