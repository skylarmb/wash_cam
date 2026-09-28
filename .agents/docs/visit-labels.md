# Visit labels

Visit labels are the ground truth for when cars arrive, wash, and leave. The site is coin-op with no sales records, so they are the only check on paid time. `wash_cam.visit_labels` stores them in migration `005`:
- A `visit_windows` row is one labeler's complete pass over one wash bay's captures in `[start_at, end_at]`, inclusive and bounded by `image_detections.captured_at`. Because every frame in a window was reviewed, a frame without events means nothing changed there.
- `labeled_by` is NULL for the human (dashboard) and `agent:<name>/<model>` for an agent. `completed_at` marks the pass finished.
- A `visit_events` row puts `arrive`, `wash_start`, `wash_stop`, or `leave` on a frame. `arrive`/`leave` mark the first and last frames a car is in the bay; a swap is `leave` on one car's last frame and `arrive` on the next car's first. `wash_start`/`wash_stop` mark the first and last frames of active cleaning, and a customer's own bucket counts. A frame can hold several events, applied in that order.

`derive_visits` turns events into visits and lists inconsistencies (e.g. a wash start with no car) without dropping them, and `frame_states` gives each frame `empty`, `car`, or `washing`. Only wash bays (`LABEL_BAYS`) are labeled. The meter runs continuously once started, so paid time is a visit's first wash start to its last wash stop.

The dashboard's Visit labeling tab (`/visits`, `dashboard/visits.py`, `static/visits.js`):
- **Tasks:** "Open range" creates one human window per wash bay, and finishing one opens the next bay. An identical range is reused and an overlapping human range is refused.
- **View:** the page never shows the full frame. It shows one bay's `crop_box` (the quad's width plus margins, the car above and the apron below) with the quad outlined, served by `/api/visits/crop`.
- **Keys:** ←/→ step and hold to play (Shift is faster). A/W/S/L toggle an event on the current frame, Backspace clears it, and `[`/`]` jump between events. Enter finishes the bay; with problems left it asks for a second Enter. Every change saves immediately through `POST /api/visits/{windows,event,clear,complete,delete}`, and agent windows are read-only.
- **Agents:**
  - `wash-cam visit-export --start "YYYY-MM-DD HH:MM" --end "..."` writes `tmp/label_batches/visits_<start>_<end>/` with `crops/<bay_id>/<image>.jpg` (exactly the dashboard's view, no detections), `manifest.json` (frames, bays, `RUBRIC`), and an empty `events.jsonl`.
  - The agent writes one line per event, `{"bay_id", "image_path", "event", "labeled_by"}`, plus `{"bay_id", "done": true, "labeled_by"}` for each bay it finished.
  - `wash-cam visit-import <batch>/events.jsonl` validates everything first and imports nothing if any line fails. It replaces that agent's earlier window for the same bay and range, and never touches human windows.
  - `wash-cam visit-compare` reports, for each agent window over a completed human one: frame agreement on occupied and washing, visit counts, washing frames, and each side's inconsistencies.
