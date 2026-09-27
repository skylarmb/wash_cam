#!/usr/bin/env bash
# Install launchd agents that keep the capture loop, dashboard, detection
# watcher, and capture heartbeat running on the server: started at login,
# restarted if they exit. Also install the nightly database backup.
#
# The first capture under launchd may trigger a macOS camera permission prompt
# on the server's screen; approve it there once.
#
# Run from any directory on the server, after `uv sync --locked`. Re-running it
# reinstalls the agents with the current checkout path.
set -euo pipefail

CHECKOUT="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
LOGS="$CHECKOUT/tmp/logs"
DOMAIN="gui/$(id -u)"

mkdir -p "$AGENTS" "$LOGS"

# Build the database before either service opens it.
"$CHECKOUT/.venv/bin/wash-cam" migrate

# Map each service to its command. The capture loop exits after its DURATION;
# KeepAlive starts the next run, so capture continues indefinitely. The loop
# holds off sleep with caffeinate only while it runs, so the long-running
# services also run under caffeinate: some service always keeps the server
# awake, even in the moment between capture runs.
# The capture interval comes from CAPTURE_INTERVAL in wash_cam/config.py, which
# sessions also use, so the two cannot drift apart.
INTERVAL="$("$CHECKOUT/.venv/bin/python" -c 'from wash_cam.config import CAPTURE_INTERVAL; print(int(CAPTURE_INTERVAL))')"

for service in capture dashboard watch heartbeat backup; do
  environment=""
  # Long-running services start at login and restart when they exit.
  schedule="<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>"
  case "$service" in
    capture)
      arguments="<string>$CHECKOUT/scripts/capture_loop.sh</string>"
      environment="<key>EnvironmentVariables</key><dict><key>INTERVAL</key><string>$INTERVAL</string></dict>"
      ;;
    backup)
      # Runs once a night at 03:30, and exits after the backup.
      arguments="<string>$CHECKOUT/scripts/backup_db.sh</string>"
      schedule="<key>StartCalendarInterval</key><dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>30</integer></dict>"
      ;;
    *) arguments="<string>/usr/bin/caffeinate</string><string>-ims</string><string>$CHECKOUT/.venv/bin/wash-cam</string><string>$service</string>" ;;
  esac
  label="com.washcam.$service"
  plist="$AGENTS/$label.plist"
  cat >"$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>$arguments</array>
  $environment
  <key>WorkingDirectory</key><string>$CHECKOUT</string>
  $schedule
  <!-- Wait before restarting a crashing service instead of spinning. -->
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$LOGS/$service.log</string>
  <key>StandardErrorPath</key><string>$LOGS/$service.log</string>
</dict>
</plist>
PLIST
  # Replace any running copy so the new definition takes effect.
  launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
  # bootout returns before the old agent is gone, and bootstrapping over it
  # fails with "Input/output error", so wait up to 10s for it to unload.
  for _ in $(seq 20); do
    launchctl print "$DOMAIN/$label" >/dev/null 2>&1 || break
    sleep 0.5
  done
  launchctl bootstrap "$DOMAIN" "$plist"
  echo "Installed $label (log: $LOGS/$service.log)"
done
