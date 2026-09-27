# Label import: first batch

Owner ScarletEagle, under the deploy lock. No code or service change.

- Batch `20260926-1600_20260927-0000`: 537 frames, labeled by `agent:SilentIsland/claude-opus-5-5` without detector output. The frozen `labels.jsonl` has SHA-256 `a40934793425c0ad6ae9b8bc0ffa7414da9879d859d26971af2dd861ef379839`.
- QA (StormyCliff) blind-labeled 30 stratified frames before it saw the candidate. Agreement was 138/138 on the bays that both agents judged. StormyCliff omitted three bay_0 boundary frames that the candidate counts as 1. bay_0 and vac_0 are mostly unjudgeable at night, and the sample has no occupied bay_4.
- The database was backed up to `tmp/backups/labels-20260927-001141.sqlite3`. The import stored 537 labels and skipped 0 human rows.
- `wash-cam score-sessions --labels agent`, open and close after 2 frames: 20 true sessions, 19 predicted, 18 matched, 2 missed, 1 phantom. Occupied time was 84.3 min predicted against 88.4 min labeled. This measures agreement with the labeling agent, not accuracy.
