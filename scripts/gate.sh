#!/usr/bin/env bash
# Decides whether the tracker should run now, and in which mode.
#
# GitHub starts scheduled workflows late (often hours) and sometimes drops them,
# so instead of a narrow time window this uses CATCH-UP windows plus a
# once-per-day marker (run_state.json):
#
#   discover: any time 08:40-13:00 Boston, if it has not succeeded yet today
#   enrich:   any time 14:50-21:00 Boston, if it has not succeeded yet today
#
# Usage:
#   gate.sh decide                     -> prints/exports mode, run, date
#   gate.sh mark <mode> <YYYY-MM-DD>   -> records a successful run in run_state.json
#
# Env: EVENT, INPUT_MODE (workflow_dispatch input), STATE_FILE, GITHUB_OUTPUT,
#      FAKE_NOW="YYYY-MM-DD HH:MM" (testing only)
set -euo pipefail

STATE_FILE="${STATE_FILE:-run_state.json}"

DISCOVER_LO=520   # 08:40
DISCOVER_HI=780   # 13:00
ENRICH_LO=890     # 14:50
ENRICH_HI=1260    # 21:00  (enrich only touches rows added in the last ~12h)

get_state() {
  # prints the YYYY-MM-DD stored for key $1, or nothing
  [ -f "$STATE_FILE" ] || return 0
  grep -o "\"$1\": *\"[0-9-]*\"" "$STATE_FILE" | grep -o '[0-9]\{4\}-[0-9]\{2\}-[0-9]\{2\}' || true
}

emit() {
  echo "$1=$2"
  [ -n "${GITHUB_OUTPUT:-}" ] && echo "$1=$2" >> "$GITHUB_OUTPUT" || true
}

if [ "${1:-}" = "mark" ]; then
  mode="$2"; day="$3"
  disc="$(get_state discover)"; enr="$(get_state enrich)"
  if [ "$mode" = "discover" ]; then disc="$day"; else enr="$day"; fi
  printf '{\n  "discover": "%s",\n  "enrich": "%s"\n}\n' "$disc" "$enr" > "$STATE_FILE"
  cat "$STATE_FILE"
  exit 0
fi

if [ -n "${FAKE_NOW:-}" ]; then
  D="${FAKE_NOW% *}"; T="${FAKE_NOW#* }"; H="${T%:*}"; M="${T#*:}"
else
  D="$(TZ=America/New_York date +%F)"
  H="$(TZ=America/New_York date +%H)"
  M="$(TZ=America/New_York date +%M)"
fi
MINS=$((10#$H * 60 + 10#$M))
echo "Boston time now: $D $H:$M"

EVENT="${EVENT:-schedule}"
MODE="none"; RUN="false"

if [ "$EVENT" = "workflow_dispatch" ]; then
  # Manual runs always go through, in the mode you picked.
  MODE="${INPUT_MODE:-discover}"; RUN="true"
else
  if [ "$MINS" -ge "$DISCOVER_LO" ] && [ "$MINS" -lt "$DISCOVER_HI" ] \
     && [ "$(get_state discover)" != "$D" ]; then
    MODE="discover"; RUN="true"
  elif [ "$MINS" -ge "$ENRICH_LO" ] && [ "$MINS" -lt "$ENRICH_HI" ] \
     && [ "$(get_state enrich)" != "$D" ]; then
    MODE="enrich"; RUN="true"
  fi
fi

emit mode "$MODE"
emit run "$RUN"
emit date "$D"
echo "Decision: mode=$MODE run=$RUN"
