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

- **Server:** the Mac mini (`skylar@10.0.4.25`, checkout `~/workspace/wash_cam`)
  holds all captures in `tmp/images/` and the only database, `db/labels.sqlite3`.
  Nothing is stored on developer laptops.
- **Services:** launchd agents `com.washcam.capture`, `com.washcam.dashboard`, and
  `com.washcam.watch` run on the mini. `scripts/install_services.sh` installs them.
  Logs are in `tmp/logs/`, and the capture log is `tmp/capture.log`.
- **Capture:** one cropped frame every 30 s (`scripts/capture_loop.sh`), aligned
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

### Step 2: Timestamps and capture interval

- [ ] Add migration `003`, which adds a UTC `captured_at` column to
  `image_detections` and indexes it.
  - The watcher derives the value from the capture filename (local time,
    `%Y%m%d_%H%M%S_%f`) in the server's timezone.
  - When daylight saving time ends, one hour repeats. For that hour, the
    file's modification time decides which occurrence a capture belongs to.
  - Backfill existing rows, either with a one-off pass or by deleting the rows
    so the watcher refills them.
- [ ] Change the capture interval to 15 s by setting `INTERVAL` in the capture
  service's launchd definition in `scripts/install_services.sh`.
- [ ] Record the interval with each capture or session. The session logic needs
  to know the expected spacing between frames.

### Step 1: Sessions (empty → full → empty)

- [ ] Add a migration for a `sessions` table with these columns:
  - `id`
  - `bay_id`
  - `started_at`: UTC time of the first occupied frame
  - `ended_at`: UTC time of the last occupied frame
  - `duration_s`
  - `frames`
  - `max_count`
  - `end_reason`: `left` or `gap`
  - `detector`: the model name
- [ ] Add a `wash_cam.sessions` module that turns each bay's ordered frames
  into sessions:
  - A bay is occupied when its vehicle count is at least 1.
  - **Open:** a session opens after `OPEN_FRAMES` (2) consecutive occupied
    frames. This debounces one-frame phantoms.
  - **Close:** a session closes after `CLOSE_FRAMES` (2) consecutive empty
    frames. This bridges one-frame misses, such as a foam-covered car at night.
  - **Gap:** if frames are missing for more than `MAX_GAP_INTERVALS` (3)
    intervals, the session closes with `end_reason = gap`. It is never
    stretched across an outage.
- [ ] Keep sessions current:
  - The watcher recomputes recent sessions after each pass.
  - `wash-cam sessions --rebuild` recomputes everything.
- [ ] Write tests on synthetic frame sequences covering:
  - a clean wash
  - a one-frame dropout in the middle of a wash
  - a one-frame phantom
  - back-to-back cars with a single empty frame between them
  - a capture gap
  - a session still open at the latest frame

### Step 5: Validation

- [ ] Hand-label a few hours of consecutive frames, day and night, with true
  session start and end times per bay. Store them as labeled sessions or
  derive them from per-frame labels.
- [ ] Add a scoring command, similar to `wash-cam evaluate`, that matches
  predicted sessions to labeled ones. It reports:
  - missed sessions
  - phantom sessions
  - the error in start time, end time, and duration
- [ ] Tune `OPEN_FRAMES`, `CLOSE_FRAMES`, and the detector thresholds against
  these scores.
- [ ] Get daily revenue totals from the owner if possible. A week of totals
  would be the best check on overall accuracy.

### Step 4: Revenue model

- [ ] Wait for pricing from the user:
  - the per-minute price
  - any starting price or minimum purchase
  - the rounding increment
  - vacuum pricing, which may differ from the wash bays
- [ ] Add pricing to configuration and a function that turns a session into
  estimated revenue. Revenue is recomputed whenever prices change.

### Step 6: Analytics dashboard

- [ ] Add an Analytics tab to the dashboard with:
  - a busy-hours heatmap of sessions by weekday and hour
  - utilization per bay (share of time occupied)
  - a distribution of wash durations
  - estimated revenue by day and by hour
- [ ] Estimate missed business: find times when every wash bay was full and
  cars were waiting or left. This needs a queue region for the foreground,
  defined like `BAYS`.

### Step 7: Operations (alongside the other steps)

- [ ] **Capture heartbeat:** alert when no capture arrives for 5 minutes.
  Choose a delivery method, such as email or a push notification.
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

- What is the pricing structure, for the wash bays and for the vacuum?
- Can the owner share actual daily revenue totals?
- How should heartbeat alerts be delivered?
