# Deploy: P1/P2 label fixes and capture heartbeat

Owner ScarletEagle, under the deploy lock. The mini went from `6615591` to `0f02100` by fast-forward. QA (StormyCliff) passed `c9ca4d3` (P1/P2) and `0f02100` (heartbeat) first.

- The database was backed up to `tmp/backups/labels-20260926-235711.sqlite3` (user_version 4, 0 labels). `uv sync` and `migrate` were no-ops.
- `scripts/install_services.sh` reinstalled all four LaunchAgents, because the heartbeat is a new service. Capture restarted once.
- Checks: `com.washcam.{capture,dashboard,watch,heartbeat}` are loaded. The dashboard and `/latest.jpg` return HTTP 200. Captures advance every 15 s. The watcher stored detections for new captures. The heartbeat log shows "Checking ... every 60s", and it wrote no false "stopped" entry.
- The labeling tab is now safe for agent rows. Label import can go ahead after the labeling rubric and StormyCliff's blind check.
