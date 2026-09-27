-- Apply the schema and its version in one transaction.
BEGIN;

-- Model predictions, kept apart from image_labels so unverified output never
-- mixes with the ground truth that models are scored against.
CREATE TABLE image_detections (
    -- Store paths relative to tmp/images, including any subdirectories.
    image_path TEXT PRIMARY KEY NOT NULL,

    -- Same vehicles.bays convention as image_labels, plus the counted vehicles.
    metadata TEXT NOT NULL
        CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),

    -- Which model produced the metadata, so results can be rerun or compared.
    model TEXT NOT NULL,

    -- UTC time the detection ran.
    detected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

PRAGMA user_version = 2;

COMMIT;
