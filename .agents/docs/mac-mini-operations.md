# Mac Mini operations

The shared test server is `skylar@mini.local`. Its checkout is `/Users/skylar/workspace/wash_cam`, and its dashboard is http://mini.local:8000. User LaunchAgents manage the dashboard and detection watcher. A separate tmux capture loop takes images every 30 seconds, prevents sleep, and defaults to eight hours. Captures and the live SQLite database remain on the Mini.

Follow the remote operations runbook in `AGENTS.md`. One deployment owner holds `/Users/skylar/.wash-cam-deploy-lock` through updates, restarts, hardware tests, and verification. Deploy an agreed commit from `main`. Preserve server data, coordinate labeling and capture interruptions, and record the deployed SHA and health results before releasing ownership.
