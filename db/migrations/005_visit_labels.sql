-- Apply the schema and its version in one transaction.
BEGIN;

-- Ground truth for visits and washing: one labeler's pass over one bay's
-- captures in a time window. Every frame in the window was reviewed, so a
-- frame without events means nothing changed there. See
-- .agents/docs/visit-labels.md. Times are UTC in 'YYYY-MM-DD HH:MM:SS.SSS'
-- format and bound frames by image_detections.captured_at, inclusive.
CREATE TABLE visit_windows (
    id INTEGER PRIMARY KEY,
    bay_id TEXT NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL CHECK (end_at >= start_at),

    -- NULL for a human on the dashboard; 'agent:<name>/<model>' for an
    -- agent's import, the same convention as image_labels.
    labeled_by TEXT,

    -- When the labeler finished the window; NULL while it is in progress.
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX visit_windows_bay_start ON visit_windows (bay_id, start_at);

-- What happened at one frame of a window. arrive and leave mark the first
-- and last frames a car is in the bay; wash_start and wash_stop mark the
-- first and last frames of active cleaning. A car swap is leave on one frame
-- and arrive on the next. One frame can hold several events, applied in the
-- order arrive, wash_start, wash_stop, leave.
CREATE TABLE visit_events (
    window_id INTEGER NOT NULL REFERENCES visit_windows (id) ON DELETE CASCADE,
    image_path TEXT NOT NULL,
    event TEXT NOT NULL
        CHECK (event IN ('arrive', 'wash_start', 'wash_stop', 'leave')),
    PRIMARY KEY (window_id, image_path, event)
);

PRAGMA user_version = 5;

COMMIT;
