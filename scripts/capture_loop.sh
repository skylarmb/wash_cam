#!/usr/bin/env bash
# Capture one image on a fixed interval for an unattended collection run.
#
# Deliberately does not use `set -e`: a single failed capture (camera briefly
# busy, USB hiccup) must not end a multi-hour collection run. Failures are
# logged and the loop continues.
set -uo pipefail

cd "$(dirname "$0")/.."

INTERVAL="${INTERVAL:-30}"
DURATION="${DURATION:-28800}" # 8 hours
LOG="${LOG:-tmp/capture.log}"

# Call the installed entry point directly. `uv run` re-resolves the environment
# on every invocation, which is wasted work hundreds of times over.
WASH_CAM="./.venv/bin/wash-cam"

mkdir -p "$(dirname "$LOG")"
end=$(($(date +%s) + DURATION))

echo "$(date -Iseconds) starting: interval ${INTERVAL}s, duration ${DURATION}s" >>"$LOG"

while [ "$(date +%s)" -lt "$end" ]; do
  started=$(date +%s)
  if output=$("$WASH_CAM" camera 2>&1); then
    echo "$(date -Iseconds) $output" >>"$LOG"
  else
    echo "$(date -Iseconds) ERROR: $output" >>"$LOG"
  fi
  # Sleep only the remainder of the interval so capture time does not add drift.
  sleep_for=$((INTERVAL - ($(date +%s) - started)))
  if [ "$sleep_for" -gt 0 ]; then
    sleep "$sleep_for"
  fi
done

echo "$(date -Iseconds) finished" >>"$LOG"
