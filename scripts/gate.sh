#!/usr/bin/env bash
# Decides whether the tracker should run now, and in which mode.
#
# Everything is in IST (Asia/Kolkata, no daylight saving) so nothing ever shifts.
# Goal: discover in the Boston MORNING, enrich in the Boston AFTERNOON:
#
#   discover  ~ 7:30 PM IST  (= about 9 AM Boston in winter, 10 AM in summer)
#   enrich    ~ 1:30 AM IST  (= about 3 PM Boston in winter, 4 PM in summer)
#
# Because enrich falls after midnight IST, a "day" here is a CYCLE that rolls over
# at 12:00 noon IST, not at midnight. The cycle is named by the IST date it
# starts on, and run_state.json stores the cycle each mode last succeeded in.
#
# GitHub starts scheduled workflows late and sometimes drops them, so each mode
# has a catch-up window instead of one exact time. All triggers (the Apps Script
# clock and the GitHub cron backups) follow the same rules:
#
#   discover: first trigger between 19:00 and 00:30 IST that has not succeeded
#             yet in this cycle
#   enrich:   first trigger between 01:00 and 06:00 IST, but ONLY once discover
#             has succeeded in this cycle (it enriches the rows discover added)
#
# Anything outside those windows (for example an old trigger firing at the wrong
# time of day) simply does nothing.
#
# Trigger types:
#   schedule                          -> follows the windows above
#   workflow_dispatch, trigger=auto   -> (Apps Script) same windows, and it only
#                                        runs if the requested mode is the one due
#   workflow_dispatch, trigger=manual -> always runs the chosen mode, never
#                                        touches the cycle marker (safe for tests)
#
# Usage:
#   gate.sh decide                      -> prints/exports mode, run, mark, date
#   gate.sh mark <mode> <cycle-date>    -> records a successful run in run_state.json
#
# Env: EVENT, INPUT_MODE, INPUT_TRIGGER, STATE_FILE, GITHUB_OUTPUT,
#      FAKE_NOW="YYYY-MM-DD HH:MM" (IST wall-clock, testing only)
set -euo pipefail

STATE_FILE="${STATE_FILE:-run_state.json}"

# Minutes since the cycle start (12:00 noon IST).
DISCOVER_LO=420   # 19:00 IST
DISCOVER_HI=750   # 00:30 IST
ENRICH_LO=780     # 01:00 IST
ENRICH_HI=1080    # 06:00 IST

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
  T="${FAKE_NOW#* }"; H="${T%:*}"; M="${T#*:}"
  CYCLE="$(TZ=UTC date -d "${FAKE_NOW}:00 UTC - 12 hours" +%F)"
else
  H="$(TZ=Asia/Kolkata date +%H)"
  M="$(TZ=Asia/Kolkata date +%M)"
  CYCLE="$(TZ=Asia/Kolkata date -d '12 hours ago' +%F)"
fi
MINS=$((10#$H * 60 + 10#$M))
CM=$(( (MINS - 720 + 1440) % 1440 ))
echo "IST time now: $H:$M  | cycle: $CYCLE  | minutes into cycle: $CM"

EVENT="${EVENT:-schedule}"
TRIGGER="${INPUT_TRIGGER:-manual}"
MODE="none"; RUN="false"; MARK="false"

# Which mode (if any) is due right now?
DUE="none"
if [ "$CM" -ge "$DISCOVER_LO" ] && [ "$CM" -lt "$DISCOVER_HI" ] \
   && [ "$(get_state discover)" != "$CYCLE" ]; then
  DUE="discover"
elif [ "$CM" -ge "$ENRICH_LO" ] && [ "$CM" -lt "$ENRICH_HI" ] \
   && [ "$(get_state discover)" = "$CYCLE" ] \
   && [ "$(get_state enrich)" != "$CYCLE" ]; then
  DUE="enrich"
fi

if [ "$EVENT" = "workflow_dispatch" ] && [ "$TRIGGER" != "auto" ]; then
  # Manual run: always goes through, in the mode you picked, no marker.
  MODE="${INPUT_MODE:-discover}"; RUN="true"
elif [ "$EVENT" = "workflow_dispatch" ]; then
  # Apps Script clock: only runs if the mode it asked for is the one that is due.
  MODE="${INPUT_MODE:-discover}"
  if [ "$DUE" = "$MODE" ]; then RUN="true"; MARK="true"; fi
else
  # GitHub cron backup.
  if [ "$DUE" != "none" ]; then MODE="$DUE"; RUN="true"; MARK="true"; fi
fi

emit mode "$MODE"
emit run "$RUN"
emit mark "$MARK"
emit date "$CYCLE"
echo "Decision: mode=$MODE run=$RUN due=$DUE trigger=$EVENT/$TRIGGER"
