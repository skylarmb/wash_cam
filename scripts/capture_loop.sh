#!/usr/bin/env bash
# Capture one image on a fixed interval for an unattended collection run.
#
# Deliberately does not use `set -e`: a single failed capture (camera briefly
# busy, USB hiccup) must not end a multi-hour collection run. Failures are
# logged and the loop continues.
set -uo pipefail

# An idle Mac suspends this loop mid-run: the CPU stops and the USB camera
# powers down, so nothing can be captured while asleep. Re-exec under
# caffeinate to hold sleep off for exactly as long as the run lasts.
#   -i no idle system sleep   -m no disk sleep   -s no system sleep (on AC)
# Display sleep is deliberately left alone; the screen can turn off without
# affecting capture.
if [ -z "${CAPTURE_LOOP_CAFFEINATED:-}" ] && command -v caffeinate >/dev/null; then
  export CAPTURE_LOOP_CAFFEINATED=1
  exec caffeinate -ims "$0" "$@"
fi

cd "$(dirname "$0")/.."

# The launchd service passes CAPTURE_INTERVAL from wash_cam/config.py.
INTERVAL="${INTERVAL:-15}"
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
  # A gap far longer than the interval means the machine was suspended rather
  # than the capture being slow. Record it so lost time is visible in the log.
  if [ -n "${previous:-}" ] && [ $((started - previous)) -gt $((INTERVAL * 3)) ]; then
    echo "$(date -Iseconds) WARNING: $((started - previous))s since previous capture (suspended?)" >>"$LOG"
  fi
  previous=$started
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
