# Nightly database backups

`scripts/backup_db.sh` backs up `db/labels.sqlite3` with SQLite's online `.backup` command. The backup is consistent while the dashboard and watcher write. It writes `tmp/backups/labels-YYYYmmdd-HHMMSS.sqlite3` and keeps the newest 7 backups there. It deletes older `labels-*.sqlite3` files and does not touch other files. The `DB`, `DEST`, and `KEEP` environment variables override the defaults. The manual pre-migration backups in `AGENTS.md` use the same directory and name pattern, so pruning also counts them.

The LaunchAgent `com.washcam.backup` runs the script each night at 03:30. It has no `KeepAlive` and exits after each run. `scripts/install_services.sh` installs it with the other services, and it logs to `tmp/logs/backup.log`. launchd runs a missed run after the Mini wakes. The backups stay on the Mini's internal disk, so they do not protect against loss of that disk.
