# Wash Cam

Read README.md for main project info.

## Shared Mac Mini

The Mini is the shared live test server. Connect with `ssh skylar@10.0.4.25`.
Its checkout is `/Users/skylar/workspace/wash_cam`. Its dashboard is http://10.0.4.25:8000.
The checkout uses `main` and `origin` (`git@github.com:skylarmb/wash_cam.git`).
Use Git to transfer code. Do not copy a local checkout or virtual environment over the server.

### Services and data

Verified on 2026-09-27 at server commit `4d053ce`:

| Component | Management | Output |
| --- | --- | --- |
| Dashboard | LaunchAgent `com.washcam.dashboard` | Port 8000, `tmp/logs/dashboard.log` |
| Detection watcher | LaunchAgent `com.washcam.watch` | `db/labels.sqlite3`, `tmp/logs/watch.log` |
| Camera capture | LaunchAgent `com.washcam.capture` | `tmp/images/`, `tmp/capture.log`, `tmp/logs/capture.log` |

LaunchAgent definitions reside in `~/Library/LaunchAgents/` with the label plus `.plist` as their names.
`scripts/install_services.sh` writes them. They run `.venv/bin/wash-cam dashboard`, `.venv/bin/wash-cam watch`,
and `scripts/capture_loop.sh` from the server checkout.
They start at user login and restart after exit. They require Skylar's GUI login session.
An SSH connection alone does not establish that session. Do not use `sudo` or the system launchd domain.
The dashboard has a Werkzeug reload parent and child. Two dashboard processes are normal.
Python edits can reload the dashboard immediately. The watcher requires an explicit restart after code changes.
Refresh the browser after dashboard changes.

The watcher scans captures every five seconds and stores results for images without detections.
It does not capture images or recompute existing detections after a model change.
The capture service passes `INTERVAL` from `CAPTURE_INTERVAL` in `src/wash_cam/config.py` (15 seconds).
Each capture loop run lasts eight hours. Then launchd `KeepAlive` starts the next run.
The loop uses `caffeinate -ims` to prevent sleep and starts a new camera process for each capture.

Keep `config.yaml`, calibration files, captures, model caches, and the live database on the Mini.
At the verified server revision, Git ignores `db/labels.sqlite3` and its journals.
Older revisions tracked that database and lacked `watch`, `migrate`, and `scripts/install_services.sh`.
Check the server revision before using instructions from an older local checkout.
Never replace the live database with a local copy or delete captures to clean the checkout.
Use the live dashboard for labeling. Coordinate labeling sessions before database maintenance.

### Inspect without changing services

Run these commands from an SSH shell on the Mini:

```sh
cd /Users/skylar/workspace/wash_cam
git status --short
git branch --show-current
git log -3 --oneline
launchctl list | rg 'com\.washcam\.'
launchctl print "gui/$(id -u)/com.washcam.dashboard" | head -45
launchctl print "gui/$(id -u)/com.washcam.watch" | head -45
launchctl print "gui/$(id -u)/com.washcam.capture" | head -45
ps -axo pid,ppid,etime,command | rg '[c]apture_loop.sh|[w]ash-cam'
tail -10 tmp/capture.log
tail -c 4000 tmp/logs/watch.log | tr '\r' '\n' | tail -20
tail -20 tmp/logs/dashboard.log
curl --fail --max-time 10 -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/
curl --fail --max-time 10 -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/latest.jpg
```

Check the LAN URL from the client too. Local HTTP success does not prove LAN access.
Check that capture timestamps advance across an interval and that the watcher stores new detections.
A running process and an old image do not prove that capture works.
Bound watcher log reads by bytes because model download progress can occupy one very long line.

### Coordinate agents before deployment

All agents share one server checkout, camera, database, and port. A Git push alone does not deploy code.
Develop and run tests in separate local branches or worktrees. Integrate changes into `main` before deployment.
Do not switch the live checkout to a feature branch or force-push shared history.
Agree on one deployment owner and an exact target commit before changing the server.
Include the purpose, affected services, and expected test window in the handoff.
Other agents can develop and push during that window, but must not pull, edit, restart, or migrate the live server.

The deployment owner must acquire this shared lock on the Mini before any mutation.
The lock is a coordination convention for agents, not a service-enforced lock.
Replace the owner text with an agent/session identifier, target SHA, and purpose:

```sh
# Acquire once. If mkdir fails, stop and contact the recorded owner.
mkdir /Users/skylar/.wash-cam-deploy-lock &&
  printf '%s\n' 'OWNER / TARGET_SHA / PURPOSE' "$(date -Iseconds)" > /Users/skylar/.wash-cam-deploy-lock/owner
```

If the directory exists, read its `owner` file and wait for the owner to finish.
Do not overwrite the owner file after a failed `mkdir`.
Do not remove a lock because it is old. Confirm that its owner has finished or explicitly handed it over.
Hold the lock through deployment, hardware tests, recovery, and health checks.
Read-only inspection does not require the lock. Camera tests and calibration require it and a paused capture loop.
Record handoffs in `.agents/notes/` and share them through the active agent coordination channel.
Notes alone do not provide mutual exclusion. Agents in separate sessions must check the server lock.

### Pull and restart

Run the project checks locally before pushing. Record the tested commit SHA.
Acquire the server lock, inspect the checkout, and record its current SHA as the recovery reference.
Stop if the checkout is dirty, on another branch, or contains unexpected commits. Coordinate with its owner.
Do not stash another agent's work, run `git reset --hard`, or run `git clean` on the Mini.

Fetch and select the agreed commit while services still run. Replace the placeholder before running:

```sh
cd /Users/skylar/workspace/wash_cam
target_sha=REPLACE_WITH_FULL_TESTED_COMMIT_SHA
git rev-parse HEAD
git fetch origin
git merge-base --is-ancestor HEAD "$target_sha"
git merge-base --is-ancestor "$target_sha" origin/main
git diff --stat HEAD "$target_sha"
```

Stop if either ancestry check fails. Review dependencies, migrations, and camera changes before proceeding.
Pause capture before dependency updates or camera changes. See the capture procedure below.
Pause labeling before database changes. Create a consistent backup with SQLite before migrations:

```sh
mkdir -p tmp/backups
sqlite3 db/labels.sqlite3 ".backup 'tmp/backups/labels-$(date +%Y%m%d-%H%M%S).sqlite3'"
```

Stop managed services before changing files to prevent reloads during a partial update.
Run each command separately and check its result. Stop on failure:

```sh
launchctl bootout "gui/$(id -u)/com.washcam.dashboard"
launchctl bootout "gui/$(id -u)/com.washcam.watch"
git merge --ff-only "$target_sha"
uv sync --locked
.venv/bin/wash-cam migrate
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.washcam.dashboard.plist"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.washcam.watch.plist"
git rev-parse HEAD
```

The fetch and fast-forward merge pull exactly the agreed revision, even if another agent pushes during testing.
Use `scripts/install_services.sh` for initial installation or changed service definitions after synchronization.
That script migrates the database and replaces both LaunchAgents. Do not run it for a single-service restart.
For a restart without a code update, use the required service only:

```sh
launchctl kickstart -k "gui/$(id -u)/com.washcam.dashboard"
launchctl kickstart -k "gui/$(id -u)/com.washcam.watch"
```

Use `bootout` for a deliberate stop. Killing a process alone lets `KeepAlive` restart it.
Do not start another dashboard on port 8000 or another watcher beside the managed services.
If deployment fails, retain ownership and report the failed step and service state.
Coordinate a compatible recovery commit. Do not blindly downgrade code after a database migration or restore over newer labels.
Restart any paused capture loop, then repeat the HTTP, log, capture, and detection checks.
Record the previous SHA, deployed SHA, checks, service changes, and unresolved issues in the handoff.
Release only your own lock after the test window ends and the handoff is complete:

```sh
rm /Users/skylar/.wash-cam-deploy-lock/owner
rmdir /Users/skylar/.wash-cam-deploy-lock
```

### Manage the camera loop

The LaunchAgent `com.washcam.capture` runs the capture loop. Do not start the loop by hand.
A second loop competes with the service for the camera. Pausing and resuming capture requires the deploy lock.

Pause capture:

```sh
launchctl bootout "gui/$(id -u)/com.washcam.capture"
```

Verify that the loop and its current camera child have exited before hardware tests or dependency updates:
`ps -axo pid,command | rg '[c]apture_loop.sh'` prints nothing.

Resume capture after maintenance:

```sh
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.washcam.capture.plist
```

To change the interval, edit `CAPTURE_INTERVAL` in `src/wash_cam/config.py`, then run `scripts/install_services.sh`.
Sessions use the same value, so do not set `INTERVAL` anywhere else.
Do not run manual camera or calibration commands while capture runs.
Camera access depends on macOS privacy permissions for the launching process.
Under launchd, the permission belongs to the virtualenv's Nix Python. See `.agents/docs/detection-watcher.md`.
If capture fails, inspect the log and the Mini's camera permissions instead of repeatedly restarting unrelated services.

## Testing

Use pytest for unit tests. Run `uv sync --locked` to install the development dependencies.
Write deterministic tests of observable behavior. Avoid external services, camera hardware, timing dependencies, and assertions against private implementation details.

Follow this workflow for new behavior and bug fixes:

1. **Red:** Write a focused test before changing behavior. Run it and confirm that it fails for the expected behavioral reason. Collection, import, and environment errors do not count as red.
2. **Green:** Make the smallest implementation change that passes the test. Do not weaken assertions to accept incorrect behavior.
3. **Refactor:** Simplify code only when needed. Keep tests green and avoid unrelated changes.
4. **Complete:** Run the full suite, build, and lint checks below. Record the red and green commands and outcomes briefly in the task notes.

```sh
uv run --locked pytest
ruff check .
ruff format --check .
uv build
git diff --check
```

Use `uv run --locked pytest tests/test_cli.py::test_dummy_command` as an example of a focused test command.

Exceptions:

- Tests that document existing behavior, including the initial suite, can start green. Do not break working code to manufacture a failure.
- Documentation and tooling changes do not require an artificial failing unit test. Run the checks appropriate to the change.
- Behavior-preserving refactors require passing relevant tests before and after the change.
- If a behavioral test cannot run, report the blocker before implementation. Do not claim an unobserved red or green result.

## Agents

### Notes

Agent notes are stored in `.agents/notes/` directory with the file name starting with the current date and time in format: `date +%Y-%m-%d-%H-%M-%S`

Once finished with a task, add notes about what was done in each project phase or feature you worked on to share context with future agents.

This is a shared scratchpad for all agents, anything can go here that you think is useful.

### Agent Mail

Agents on all machines coordinate through MCP Agent Mail. The server runs on the Mac mini (`mini`) and is available on the Tailscale tailnet at `http://mini.tail951c67.ts.net:8765/api/`.

If the `mcp-agent-mail` tools are not available to you, onboard your machine:

1. Make sure the machine is on the tailnet. Run `tailscale ping mini` to check.
2. Get the bearer token over SSH from the mini. The token is `HTTP_BEARER_TOKEN` in `~/.local/share/mcp_agent_mail/.env` on the mini. Do not print the token, write it to the repo, or commit it.
3. Add the server to your MCP config. For Claude Code, run this as one command:

   ```sh
   claude mcp add --transport http --scope user mcp-agent-mail http://mini.tail951c67.ts.net:8765/api/ --header "Authorization: Bearer $(ssh skylar@mini "sed -n 's/^HTTP_BEARER_TOKEN=//p' ~/.local/share/mcp_agent_mail/.env")"
   ```

   For other agents, add an HTTP MCP server with the same URL and `Authorization` header.
   If `ssh` fails with a host key prompt or error, stop and ask the user to connect to `mini` one time by hand.
4. Restart the agent. Call `health_check` and make sure that it returns `"status": "ok"`.
5. Set up the mail watcher (see "Wait for mail" below). If you are not on the mini, copy the watcher script and write the env file. Run this as one command:

   ```sh
   mkdir -p ~/.local/share/mcp_agent_mail/scripts/hooks ~/.config/agent_mail &&
   scp skylar@mini:.local/share/mcp_agent_mail/scripts/hooks/check_inbox.sh ~/.local/share/mcp_agent_mail/scripts/hooks/ &&
   (umask 077; printf 'AGENT_MAIL_PROJECT=/Users/skylar/workspace/wash_cam\nAGENT_MAIL_URL=http://mini.tail951c67.ts.net:8765/api/\nAGENT_MAIL_TOKEN=%s\n' "$(ssh skylar@mini "sed -n 's/^HTTP_BEARER_TOKEN=//p' ~/.local/share/mcp_agent_mail/.env")" > ~/.config/agent_mail/env)
   ```

   The env file has mode 600 and stays outside the repo. On the mini, the script and the env file already exist.

Rules for every task:

- Use the project key `/Users/skylar/workspace/wash_cam` as `human_key` and `project_key`. Use this key even if your checkout is at a different path. Agents share a project only when they use the same key.
- At the start of a task, call `macro_start_session`. This registers your agent and returns your inbox.
- Before you edit files, reserve them with `file_reservation_paths`. After you finish, release them with `release_file_reservations`.
- Check your inbox with `fetch_inbox` between steps, and reply to messages that ask you something.
- After you handle a message, mark it read with `mark_message_read`, or acknowledge it with `acknowledge_message`. The watcher counts unread mail, so an unread message wakes you again.
- `mcp-agent-mail` is not a shell command. Use only the MCP tools.

#### Wait for mail

Mail does not wake an idle agent. Claude Code starts a new turn when a background command exits. The watcher uses this: it blocks until you have unread mail, then exits.

1. Before you end a turn with no work left, start the watcher as one Bash command with `run_in_background`. Replace the two placeholders with your values from `macro_start_session`:

   ```sh
   set -a; . ~/.config/agent_mail/env; AGENT_MAIL_AGENT=<agent name>; AGENT_MAIL_REGISTRATION_TOKEN=<registration token>; AGENT_MAIL_INTERVAL=0; set +a
   until out=$(~/.local/share/mcp_agent_mail/scripts/hooks/check_inbox.sh); [ -n "$out" ]; do sleep 15; done; echo "$out"
   ```

2. When the watcher exits, read your unread mail with `fetch_inbox` and `unread_only: true`.
3. Handle each message, then mark it read or acknowledge it.
4. Start the watcher again.

Start only one watcher at a time. The watcher calls the server with `curl` every 15 seconds and uses no model tokens. It stops when the session closes.

A busy agent can also get mail reminders between tool calls from a `PostToolUse` hook that runs `check_inbox.sh` with `AGENT_MAIL_INTERVAL=60`. This hook changes the user's Claude Code settings, so add it only if the user asks for it.

### Docs

For each feature completed, write a SHORT documentation file (no more than 3 paragraphs) in `.agents/docs/` directory with the file name corresponding to the feature name. This is the official briefing for other agents. Do not include implementation process or project phase details here, only document the current working version of the code.
