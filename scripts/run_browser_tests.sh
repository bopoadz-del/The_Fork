#!/usr/bin/env bash
# Run the Playwright-driven browser test suite.
#
# Usage:
#   scripts/run_browser_tests.sh                 # all tests, headless
#   scripts/run_browser_tests.sh -k chat         # only matching tests
#   HEADED=1 scripts/run_browser_tests.sh        # show the browser
#   scripts/run_browser_tests.sh --slowmo=500    # extra args to pytest
#
# First-time setup is idempotent — it installs Playwright browsers + system deps
# only if they're missing.
set -euo pipefail

cd "$(dirname "$0")/.."

# 1. Make sure Playwright + Chromium + system libs are present.
if ! python -c "import playwright" 2>/dev/null; then
  echo "Installing Playwright Python package..."
  pip install --quiet playwright pytest-playwright
fi

if [ ! -d "$HOME/.cache/ms-playwright" ] || ! ls "$HOME/.cache/ms-playwright" 2>/dev/null | grep -q "chromium"; then
  echo "Installing Chromium for Playwright..."
  python -m playwright install chromium
fi

# Quick check for libatk — if missing, we need system deps.
if ! ldconfig -p 2>/dev/null | grep -q "libatk-1.0.so.0"; then
  echo "Installing system libs for Chromium (sudo required)..."
  sudo python -m playwright install-deps chromium
fi

# 2. Run the suite.
extra_args=()
if [ "${HEADED:-}" = "1" ]; then
  extra_args+=("--headed")
fi

exec python -m pytest tests/browser/ -v --tb=short "${extra_args[@]}" "$@"
