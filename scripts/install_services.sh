#!/usr/bin/env bash
# Install launchd agents that keep the dashboard and detection watcher running
# on the server: started at login, restarted if they exit.
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

for service in dashboard watch; do
  label="com.washcam.$service"
  plist="$AGENTS/$label.plist"
  cat >"$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>$CHECKOUT/.venv/bin/wash-cam</string>
    <string>$service</string>
  </array>
  <key>WorkingDirectory</key><string>$CHECKOUT</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <!-- Wait before restarting a crashing service instead of spinning. -->
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$LOGS/$service.log</string>
  <key>StandardErrorPath</key><string>$LOGS/$service.log</string>
</dict>
</plist>
PLIST
  # Replace any running copy so the new definition takes effect.
  launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$plist"
  echo "Installed $label (log: $LOGS/$service.log)"
done
