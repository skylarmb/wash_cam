# Mac Mini guidance

Inspected the Mini over SSH without modifying its checkout, data, services, or tmux panes. Local HEAD was `c3d0048`. The clean server checkout used `main` at `0ad5486b10d8de5c5604063c9488b657bafdd661`. The newer server revision added detection storage, migrations, ignored database files, and the service installer. No local pull or deployment was performed.

Confirmed user LaunchAgents `com.washcam.dashboard` and `com.washcam.watch`, their checkout paths, logs, and restart policy. Capture ran in tmux pane `main:1.2`; another agent occupied `main:1.1`. Capture timestamps advanced by 30 seconds. The watcher processed 241 backlog images and subsequent captures. Both `/` and `/latest.jpg` returned HTTP 200 through server localhost. Dashboard logs also showed successful LAN requests. No new client-side LAN request was made.

Added the AGENTS.md runbook and the operations briefing. The runbook defines a cooperative server lock, exact-commit deployment, launchd controls, database protection, capture management, recovery constraints, and agent handoffs. The lock is a new convention. It was not created on the Mini, and mutation commands were not exercised against live services.

Validation: documentation-only change, so no artificial red test. `uv run --locked pytest` passed all 126 tests. `ruff check .`, `ruff format --check .`, `uv build`, and `git diff --check` passed. Initial uv commands failed on sandbox cache access; reruns with approved cache access passed.
