# Deploy handoff: label tools (6615591) and first labeling batch

Owner SapphireCompass, approved by ScarletEagle. Previous SHA `037dda649c92b3107576996939e241d8abe25b85`, deployed `66155912f23ecf8a5507886e5992c318c4d01f5f`. Pre-checks passed (lock, clean checkout, both ancestry checks, no dependency/migration/camera changes). Database backed up to `tmp/backups/`; dashboard and watcher booted out, fast-forwarded, `uv sync --locked` and `migrate` no-ops, both bootstrapped; capture kept running.

Checks: dashboard HTTP 200; captures continuing every 15 s; restarted watcher stored a detection 5 s after capture; `score-sessions` reports "Scored against agent labels". Exported the first batch under the lock: `tmp/label_batches/20260926-1600_20260927-0000/`, 537 frames from 16:46:49 to 23:42:06 PDT, 63 MB of overlays. Lock released. Open: the labeling task has no owner after StormyCliff moved to QA.
