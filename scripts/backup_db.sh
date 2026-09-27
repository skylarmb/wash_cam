#!/usr/bin/env bash
# Back up the label database with SQLite's online .backup, which is consistent
# while the dashboard and watcher write, and keep only the newest KEEP nightly copies.
# Only nightly-* files are pruned; manual labels-* recovery backups are kept.
# The LaunchAgent com.washcam.backup runs it nightly; see install_services.sh.
set -euo pipefail

CHECKOUT="$(cd "$(dirname "$0")/.." && pwd)"
DB="${DB:-$CHECKOUT/db/labels.sqlite3}"
DEST="${DEST:-$CHECKOUT/tmp/backups}"
KEEP="${KEEP:-7}"

mkdir -p "$DEST"
out="$DEST/nightly-$(date +%Y%m%d-%H%M%S).sqlite3"
sqlite3 "$DB" ".backup '$out'"
echo "$(date -Iseconds) backed up $DB to $out"

# Timestamped names sort by age, so delete all but the newest KEEP. macOS head
# has no negative counts, so list newest first and skip the first KEEP.
ls "$DEST"/nightly-*.sqlite3 | sort -r | tail -n "+$((KEEP + 1))" | while read -r old; do
  rm -- "$old"
done
