-- Apply the schema and its version in one transaction.
BEGIN;

CREATE TABLE image_labels (
    -- Store paths relative to images/, including any subdirectories.
    image_path TEXT PRIMARY KEY NOT NULL,

    -- Keep label fields flexible, but require a valid JSON object.
    metadata TEXT NOT NULL
        CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),

    -- Verification covers the entire image. Store the time in UTC.
    verified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

PRAGMA user_version = 1;

COMMIT;
