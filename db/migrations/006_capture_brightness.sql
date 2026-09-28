-- Apply the schema and its version in one transaction.
BEGIN;

-- Mean brightness (0-255) of the stored capture. Captures darker than
-- config.UNUSABLE_BRIGHTNESS show nothing usable, so sessions and analytics
-- treat them as an outage. NULL until the watcher measures the image; the
-- watcher backfills missing values at startup.
ALTER TABLE image_detections ADD COLUMN brightness REAL;

PRAGMA user_version = 6;

COMMIT;
