-- Apply the schema and its version in one transaction.
BEGIN;

-- One vehicle visit to one bay, derived from image_detections by
-- wash_cam.sessions. Rebuildable at any time, so never edit rows by hand.
-- Times are UTC in 'YYYY-MM-DD HH:MM:SS.SSS' format.
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY,
    bay_id TEXT NOT NULL,

    -- Estimated arrival and departure: halfway between the first or last
    -- occupied frame and its empty neighbor, or the frame itself when the
    -- neighbor is missing (start of data, or a capture gap).
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,

    -- Times of the first and last frames that saw the bay occupied.
    first_frame_at TEXT NOT NULL,
    last_frame_at TEXT NOT NULL,

    duration_s REAL NOT NULL,
    -- Occupied frames in the visit, and the most vehicles seen at once.
    frames INTEGER NOT NULL,
    max_count INTEGER NOT NULL,

    -- left: the bay emptied. gap: capture stopped mid-visit.
    -- open: still occupied at the latest frame.
    end_reason TEXT NOT NULL CHECK (end_reason IN ('left', 'gap', 'open'))
);

CREATE INDEX sessions_bay_started ON sessions (bay_id, started_at);

PRAGMA user_version = 4;

COMMIT;
