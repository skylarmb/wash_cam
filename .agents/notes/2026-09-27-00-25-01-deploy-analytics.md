# Deploy: Analytics tab

Owner ScarletEagle, under the deploy lock. QA (StormyCliff) passed `c83c320` with 184 tests, lint, format, and build, plus Firefox checks at desktop width and 375 px. `88aa130` merges it into `main` with docs changes only.

- The mini went from `2b83b74` to `88aa130` by fast-forward. The database was backed up first. There were no dependency or migration changes. Only the dashboard and watcher restarted. Capture and the heartbeat kept running.
- Checks: `/`, `/latest.jpg`, `/labeling`, `/analytics`, and `/analytics?days=1` return 200. The LAN URL `http://10.0.4.25:8000/analytics` returns 200. Captures advance, and the watcher stores detections.
- Follow-ups: histogram labels are crowded at 375 px, and the vacuum p90 extrapolates above the observed maximum. SilentIsland fixes the p90 with the revenue work.
