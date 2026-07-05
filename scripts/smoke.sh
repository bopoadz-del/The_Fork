#!/usr/bin/env bash
# scripts/smoke.sh - pre-deploy smoke: the 10-run deliverable discriminator.
#
# Drives fork_cli against a live Fork instance ten times with a deliverable
# (tool-calling) prompt and prints a per-run summary + a single verdict line.
# This is the tripwire for the reasoning-field 400 class of bug: a deliverable
# turn that errors produces an empty answer (answer_chars=0), and the tool path
# is exactly what breaks - so we require both a full success rate AND that the
# tool path actually fired on several runs.
#
# VERDICT: PASS iff all runs succeed (answer_chars > 0) AND >= 3 runs contain a
#          tool_call/tool_result pair. Otherwise FAIL with a nonzero exit code.
#
# Credentials come from the environment ONLY - never hardcode a key here:
#   export FORK_API_KEY="<master key>"           # preferred
#   -- or -- FORK_EMAIL + FORK_PASSWORD
#   -- or -- FORK_TOKEN
# Target defaults to prod (fork_cli's own default); override with FORK_BASE_URL.
#
# Usage:
#   FORK_API_KEY="..." ./scripts/smoke.sh
#   ./scripts/smoke.sh dar_al_arkan_master
set -u

PROJECT="${1:-${FORK_PROJECT:-dar_al_arkan_master}}"
RUNS="${SMOKE_RUNS:-10}"
MESSAGE="${SMOKE_MESSAGE:-generate a commissioning checklist for the MV substation}"
export PYTHONIOENCODING=utf-8

# --- credential guard: env only, never hardcoded ---
if [ -z "${FORK_API_KEY:-}" ] \
   && { [ -z "${FORK_EMAIL:-}" ] || [ -z "${FORK_PASSWORD:-}" ]; } \
   && [ -z "${FORK_TOKEN:-}" ]; then
  echo "No credentials in env. Set FORK_API_KEY (or FORK_EMAIL + FORK_PASSWORD, or FORK_TOKEN)." >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI="$SCRIPT_DIR/fork_cli.py"
[ -f "$CLI" ] || { echo "fork_cli.py not found next to smoke.sh ($CLI)" >&2; exit 2; }

BASE_ARGS=()
[ -n "${FORK_BASE_URL:-}" ] && BASE_ARGS=(--base "$FORK_BASE_URL")

successes=0
tool_runs=0

echo "Pre-deploy smoke: $RUNS runs, project=$PROJECT"
echo "message: $MESSAGE"
printf '=%.0s' $(seq 1 72); echo

for i in $(seq 1 "$RUNS"); do
  out="$(python "$CLI" "${BASE_ARGS[@]}" chat "$MESSAGE" --project "$PROJECT" --events 2>&1)"

  line="$(printf '%s\n' "$out" | grep -oE 'total=[0-9.]+s +first_token=([0-9.]+s?|-) +events=[0-9]+ +answer_chars=[0-9]+' | head -1)"
  chars="$(printf '%s\n' "$line" | grep -oE 'answer_chars=[0-9]+' | grep -oE '[0-9]+$')"
  total="$(printf '%s\n' "$line" | grep -oE 'total=[0-9.]+' | grep -oE '[0-9.]+')"
  ftok="$(printf '%s\n' "$line" | sed -nE 's/.*first_token=([0-9.]+s?|-).*/\1/p')"
  chars="${chars:-0}"

  has_tool=n
  if printf '%s\n' "$out" | grep -q 'TOOL_CALL' && printf '%s\n' "$out" | grep -q 'TOOL_RESULT'; then
    has_tool=Y
  fi

  ok="FAIL"
  if [ "${chars:-0}" -gt 0 ] 2>/dev/null; then
    ok="OK  "
    successes=$((successes + 1))
  fi
  [ "$has_tool" = "Y" ] && tool_runs=$((tool_runs + 1))

  printf 'run%2d  %s  tool=%s  total=%ss  first_token=%s  answer_chars=%s\n' \
    "$i" "$ok" "$has_tool" "${total:-?}" "${ftok:-?}" "$chars"
done

printf '=%.0s' $(seq 1 72); echo
if [ "$successes" -eq "$RUNS" ] && [ "$tool_runs" -ge 3 ]; then
  echo "VERDICT: PASS  ($successes/$RUNS succeeded, $tool_runs runs with tool_call/tool_result)"
  exit 0
else
  echo "VERDICT: FAIL  ($successes/$RUNS succeeded, $tool_runs with tool pair; need $RUNS/$RUNS and >=3 tool)"
  exit 1
fi
