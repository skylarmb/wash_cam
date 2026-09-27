# Deploy handoff: score-sessions (fa78c9f)

Owner SapphireCompass, approved by ScarletEagle. Previous SHA `4d053cef289015fead0ee9a8e411e5dde7c7951c`, deployed `fa78c9f9f7eb1c2cba862c4724fcb9a749480369`. Pre-checks: lock acquired, clean checkout on `main`, both ancestry checks passed, no dependency/migration/camera changes. Backed up the database to `tmp/backups/`, booted out dashboard and watcher, fast-forwarded, `uv sync --locked` and `migrate` (no-ops, user_version 4), bootstrapped both. Capture (`com.washcam.capture`) kept running.

Checks: dashboard HTTP 200; captures continued every 15 s; the restarted watcher stored a detection 2 s after capture; `wash-cam score-sessions` runs (all zero, no labels yet). Lock released. No unresolved issues.
