# Image labels

`db/labels.sqlite3` contains the `image_labels` table. Each row stores the current verified labels for one image. `image_path` is the primary key and holds a path relative to `images/`. `metadata` stores a valid JSON object as text. `verified_at` stores the UTC verification time and defaults to the insertion time. Verification covers the entire image. Updates must set the new verification time explicitly.

The metadata convention uses `vehicles.bays`, an array of entries with stable `bay_id` values and flexible label fields. An empty bay has `vehicle_present: false`. An omitted bay has not been labeled. The database requires an object at the JSON root but does not enforce fields or bay IDs. An absent row means no verified labels have been saved.

`db/migrations/001_image_labels.sql` creates the schema and sets `PRAGMA user_version` to `1` in one transaction. The tracked database already contains this migration. Git tracks the database and ignores temporary journal files. Only one developer modifies the database at a time. Pull before labeling, close the connection before committing, and push before the next developer starts. Images stay on disk. The project has no database input interface or migration runner.
