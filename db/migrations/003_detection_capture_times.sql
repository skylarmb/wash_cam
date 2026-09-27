-- Apply the schema and its version in one transaction.
BEGIN;

-- UTC time the capture was taken, as 'YYYY-MM-DD HH:MM:SS.SSS'. Filenames are
-- local time and repeat an hour when daylight saving ends, so sessions order
-- and time frames by this instead. Null until the watcher fills it in.
ALTER TABLE image_detections ADD COLUMN captured_at TEXT;

CREATE INDEX image_detections_captured_at ON image_detections (captured_at);

PRAGMA user_version = 3;

COMMIT;
