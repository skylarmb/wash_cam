# SilentIsland handoff (2026-09-27 night)

SilentIsland (Claude Code, worktree `wt-dev-agent` on sky-mbp, same machine as StormyCliff) took over SapphireCompass's queue. Live now: first agent label batch (537 frames, 138/138 blind agreement), Analytics tab, estimated revenue with date sort and inclusive p90, nightly backups (`nightly-*`, 03:30), and the labeling navigation guard. Per-feature notes are alongside this file.

Rules learned: only ScarletEagle merges to `main` and deploys; send QA-passed branch SHAs. Agent Mail works over the LAN at `http://10.0.4.25:8765/api/`. Labeling checks use a snapshot, never the live tab.

Next: daytime labeling batch once daylight captures exist (export, rubric, StormyCliff blind check, import with ScarletEagle's OK), then retest `close 4`. Parked: image pruning (Skylar freed space), histogram labels at 375 px. Open for Skylar: the all-zero row for `20260927_001801_429035.jpg`.
