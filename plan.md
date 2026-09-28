# Wash Cam Plan

Living plan for turning per-image vehicle detections into revenue estimates and
busy-hour analytics. Update the status boxes and decisions as work lands, so
the plan stays accurate across sessions and agents.

## Goal

Estimate car wash revenue and answer questions such as:

- Which hours and weekdays are busiest?
- How long does a typical wash take?
- How evenly are the bays used?
- How much business is missed when every bay is full?

This is a hobby project. Loose accuracy is acceptable.

## Current state (2026-09-26)

- **Server:** the Mac mini (`skylar@mini.local`, checkout `~/workspace/wash_cam`)
  holds all captures in `tmp/images/` and the only database, `db/labels.sqlite3`.
  Nothing is stored on developer laptops.
- **Services:** launchd agents `com.washcam.capture`, `com.washcam.dashboard`, and
  `com.washcam.watch` run on the mini. `scripts/install_services.sh` installs them.
  Logs are in `tmp/logs/`, and the capture log is `tmp/capture.log`.
- **Capture:** one cropped frame every 15 s (`scripts/capture_loop.sh`), aligned
  with the calibration in `config.yaml`.
- **Detection:** `wash-cam watch` runs the pretrained COCO Faster R-CNN detector
  (`wash_cam.detect`) on every new capture. It stores per-bay counts and counted
  vehicle boxes in `image_detections`.
- **Bays:** `BAYS` in `wash_cam/config.py` defines wash bays `bay_0` to `bay_4`,
  left to right, and the vacuum station `vac_0`.
- **Labels:** verified human labels are stored in `image_labels`. They are
  entered on the dashboard's Data labeling tab. `wash-cam evaluate` scores
  models against them.

## Decisions

- **Occupied is not the same as paying.** A car can sit in a bay without the
  meter running. We accept this error for now and treat bay time as an upper
  bound on paid time. A fine-tuned model that classifies active washing
  (a person with a wand, visible spray) is planned later. See Future work.
- **Capture interval:** 15 s. Each session edge is uncertain by up to one
  interval. At 15 s, about 600 MB of images accumulate per day.
- **Derived data is rebuildable.** Sessions and revenue are recomputed from
  stored detections and never edited by hand. Changes to the detector,
  thresholds, or prices are applied with a rebuild.
- **Predictions stay separate from ground truth.** Model output goes to
  `image_detections` and derived tables, never to `image_labels`.

## Roadmap

Work proceeds in this order: 2, 1, 5, 4, 6. Step 7 runs alongside the others.
Follow the red/green workflow in `AGENTS.md` for every step.

### Owners

Owners are Agent Mail agent names. Numbered rows run in that order. ScarletEagle is the only agent that merges into `main` and deploys. Other agents push feature branches and send the SHA after QA passes. Reserve files before you edit them. Tell all
agents which migration number you take before you write it.

| Work | Main files | Owner |
|---|---|---|
| Step 5: session scorer (`score-sessions`) | `src/wash_cam/session_eval.py`, `src/wash_cam/cli.py` | SapphireCompass (done, `fa78c9f`) |
| Step 5: `label-export` and label importer | `src/wash_cam/labeling.py`, CLI commands, tests | SapphireCompass (done, `6615591`) |
| Step 7: capture heartbeat (alerts go to `NOTIFICATIONS.md`) | `src/wash_cam/heartbeat.py` and its tests, one entry in `scripts/install_services.sh` | ScarletEagle (done, deployed `0f02100`) |
| 2. Step 5: agent labeling of the first batch (StormyCliff blind-checks about 30 frames before import) | label files only, no code | SilentIsland (done: 537 frames imported, blind check 138/138) |
| 3. Step 7: nightly backups | `scripts/backup_db.sh`, one entry in `scripts/install_services.sh` | SilentIsland (done, deployed `88f4bcb`) |
| 4. Step 6: Analytics tab without revenue (heatmap, utilization, durations) | `src/wash_cam/dashboard/analytics.py` and its tests, route registration in `dashboard/__init__.py` | SapphireCompass and SilentIsland (done, deployed `88aa130`) |
| Step 5: threshold tuning (after labels are imported) | `src/wash_cam/sessions.py` | Paused (first sweep done; the `close 4` retest waits for the new labeling pass) |
| Step 5: labeling | label files only | **Paused by Skylar (2026-09-27).** The next pass adds classes such as active washing versus a parked car, and it can use other models. The rubric changes. Batch `20260927-0700_20260927-1000` (713 frames) is exported but not labeled. Keep it as data. |
| Step 4 and revenue in step 6 (pricing known) | `config.py` pricing, a revenue function, the Analytics tab | SilentIsland (done, deployed `5f0b8da` and `bd616a4`) |
| Coordination: `plan.md` status and `AGENTS.md` | `plan.md`, `AGENTS.md` | ScarletEagle |
| QA: correctness, validation, simplicity, and scope reviews | review findings only | StormyCliff |

### Step 2: Timestamps and capture interval

- [x] Add migration `003`, which adds a UTC `captured_at` column to
  `image_detections` and indexes it.
  - The watcher derives the value from the capture filename (local time,
    `%Y%m%d_%H%M%S_%f`) in the server's timezone.
  - When daylight saving time ends, one hour repeats. For that hour, the
    file's modification time decides which occurrence a capture belongs to.
  - Backfill existing rows. The watcher fills missing capture times when it
    starts (`backfill_capture_times`).
- [x] Change the capture interval to 15 s. `CAPTURE_INTERVAL` in
  `wash_cam/config.py` is the single source, and `scripts/install_services.sh`
  passes it to the capture loop.
- [x] ~~Record the interval with each capture or session.~~ Not needed:
  - Session debouncing counts frames rather than seconds.
  - Arrival and departure are estimated from the actual spacing of
    neighboring frames.
  - Older 30 s data therefore stays valid. Only gap detection uses
    `CAPTURE_INTERVAL`.

### Step 1: Sessions (empty → full → empty)

- [x] Add migration `004` for a `sessions` table with these columns:
  - `id`
  - `bay_id`
  - `started_at`: UTC time of the first occupied frame
  - `ended_at`: UTC time of the last occupied frame
  - `duration_s`
  - `frames`
  - `max_count`
  - `end_reason`: `left`, `gap`, or `open`
  - `first_frame_at` and `last_frame_at`: the raw times of the first and last
    occupied frames. `started_at` and `ended_at` are estimated halfway to the
    neighboring empty frames.
  - The planned `detector` column was dropped, because sessions are rebuilt
    whenever detections change.
- [x] Add a `wash_cam.sessions` module that turns each bay's ordered frames
  into sessions:
  - A bay is occupied when its vehicle count is at least 1.
  - **Open:** a session opens after `OPEN_FRAMES` (2) consecutive occupied
    frames. This debounces one-frame phantoms.
  - **Close:** a session closes after `CLOSE_FRAMES` (2) consecutive empty
    frames. This bridges one-frame misses, such as a foam-covered car at night.
  - **Gap:** if frames are missing for more than `MAX_GAP_INTERVALS` (3)
    intervals, the session closes with `end_reason = gap`. It is never
    stretched across an outage.
- [x] Keep sessions current:
  - The watcher recomputes recent sessions after each pass.
  - `wash-cam sessions --rebuild` recomputes everything.
- [x] Write tests on synthetic frame sequences covering:
  - a clean wash
  - a one-frame dropout in the middle of a wash
  - a one-frame phantom
  - back-to-back cars with a single empty frame between them. These merge
    into one session by design; two empty frames split them.
  - a capture gap
  - a session still open at the latest frame

### Step 5: Validation

- [ ] Label a few hours of consecutive frames, day and night, for all six
  bays. Rowan decided that an agent labels them, not a human:
  - SapphireCompass builds `label-export`, which writes images with bay
    overlays and a manifest but no detections, and an importer.
  - SapphireCompass labels with its own vision model and never reads
    `image_detections`. StormyCliff (QA) reviews the rubric and a sample of
    the labels before the first batch is imported.
  - Each agent label records `"labeled_by": "agent:<name>/<model>"` in
    `image_labels.metadata`. A later human check records `"reviewed_by"`. The
    importer never overwrites a label without `labeled_by`.
  - The scorer reports agreement with agent labels separately from accuracy
    against human labels.
- [x] Add a scoring command, `wash-cam score-sessions [--open N] [--close N]`, that matches
  predicted sessions to labeled ones. It reports:
  - missed sessions
  - phantom sessions
  - the error in start time, end time, and duration
- [ ] Tune `OPEN_FRAMES`, `CLOSE_FRAMES`, and the detector thresholds against
  these scores.
  - First sweep (2026-09-27, one evening, 20 sessions, agent labels): the
    current open 2, close 2 matched 18, with 2 misses and 1 phantom. Open 1,
    close 4 matched 19, with 1 miss and 0 phantoms. A higher open value only
    adds misses. Close 4 removes phantoms and end error. Keep open 2 and test
    close 4 again on a daytime batch before changing defaults.
- [ ] Get daily revenue totals from the owner if possible. A week of totals
  would be the best check on overall accuracy.

### Step 4: Revenue model

- [x] Wait for pricing from the user. Skylar gave it on 2026-09-27: wash
  bays cost $1 per minute with a $3 minimum per session, and the vacuum costs
  $0.25 per minute (a placeholder). No owner totals exist, so revenue is a
  rough estimate. The original questions were:
  - the per-minute price
  - any starting price or minimum purchase
  - the rounding increment
  - vacuum pricing, which may differ from the wash bays
- [ ] Add pricing to configuration and a function that turns a session into
  estimated revenue. Revenue is recomputed whenever prices change.

### Step 6: Analytics dashboard

- [x] Add an Analytics tab to the dashboard with:
  - a busy-hours heatmap of sessions by weekday and hour
  - utilization per bay (share of time occupied)
  - a distribution of wash durations
  - estimated revenue by day and by hour
- [x] Rebuild the tab as a live page over a JSON API (2026-09-27): live bay
  strip, visit timeline, bays busy at once, revenue pace, visit-length
  beeswarm, typical-day profile, bay balance, and a daily revenue calendar.
- [ ] Estimate missed business: find times when every wash bay was full and
  cars were waiting or left. This needs a queue region for the foreground,
  defined like `BAYS`. Until then, "all bays busy" time is the proxy.

### Step 7: Operations (alongside the other steps)

- [x] **Capture heartbeat:** alert when no capture arrives for 5 minutes.
  Append each alert to `NOTIFICATIONS.md` at the root of the mini's checkout.
  Git ignores this file. Skylar reviews it each morning.
- [ ] **Nightly backups:** back up the database with SQLite's `.backup`
  command, not a plain file copy, and keep a few days of history. Existing
  manual backups are in `~/wash_cam_backup/` on the mini.
- [ ] **Image storage:**
  - The mini has about 18 GB free, which lasts roughly 4 weeks at a 15 s
    interval.
  - Options:
    - move images to external storage (a 30 GB USB drive,
      `/Volumes/NO NAME`, is mounted)
    - delete old images once their detections are stored, keeping labeled and
      interesting frames
    - recompress images
  - Analytics need only the detections, not the images.
  - 2026-09-27: Skylar freed space. 45 GiB is free, which lasts about 80 days
    at about 560 MB per day. A pruning proposal (14-day retention that keeps
    labeled and batch frames, with an optional USB archive) is on hold until
    space is a concern again.

## Future work

- **Active-washing classifier:** fine-tune a vision model to classify whether
  someone is actively washing. Paid time would then replace bay time.
- **Night fine-tune:** fine-tune the detector if validation shows that night
  frames are its weak spot. Known night misses include occluded, foam-covered
  cars.
- **Crop:** widen the crop if cars at the vacuum turn out to be cut off too
  often. This changes coordinates for all earlier captures.

## Known risks

- The mini does not log in automatically, and automatic macOS updates are on.
  A reboot therefore stops collection until someone logs in. The user accepts
  this risk.
- The launchd capture agent's camera permission is tied to the Nix Python path
  on the mini. A Python upgrade needs the permission approved again on the
  mini's screen.

## Open questions

- None. The vacuum price is a placeholder, so update it when the real price is known.
