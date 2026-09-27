# Capture heartbeat

`wash-cam heartbeat` checks the newest capture in `tmp/images/` every 60 seconds. The newest capture is the one with the newest mtime. Filenames are local times, which repeat at the fall DST change. When the newest capture is older than 5 minutes, or no capture exists, capture has stopped. The LaunchAgent `com.washcam.heartbeat` runs it. `scripts/install_services.sh` installs it with the other services, and it logs to `tmp/logs/heartbeat.log`.

The heartbeat appends to `NOTIFICATIONS.md` at the checkout root. It writes one "captures stopped" entry when an outage starts and one "captures resumed" entry when captures return. That entry gives the gap between the last capture before the outage and the first capture after it. It writes nothing while the state stays the same. Git ignores `NOTIFICATIONS.md`. Skylar reads it each morning, and other agents can also append entries that need a human.

The outage state is kept only in memory. If the heartbeat starts during an outage, for example after a reboot and a login, it writes a new "captures stopped" entry. The heartbeat runs on the mini, so it cannot report that the mini itself is down or logged out.
