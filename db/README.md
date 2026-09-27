# Image labels

`labels.sqlite3` stores one verified label record per image in `image_labels`.
Git tracks the migrations and the database. Git ignores temporary journal files.
Images remain in `images/`.

Only one developer modifies the database at a time.
Pull the latest database before a labeling session.
Close the database connection before committing the database file.
Commit and push the database before another developer starts labeling.

| Column | Meaning |
| --- | --- |
| `image_path` | Unique, non-null path relative to `images/`. Include any subdirectories. |
| `metadata` | Non-null JSON object stored as text. Label fields can vary between records. |
| `verified_at` | Non-null UTC verification time. Defaults to the insertion time in `YYYY-MM-DD HH:MM:SS` format. |

Verification covers all labels for an image at once.
An absent row means no verified labels have been saved.
The table stores the current labels without revision history.
Future updates must set `verified_at` to the new verification time.
SQLite does not check whether the image file exists.

## Metadata convention

Use `vehicles.bays` for an array of bay entries.
Give each entry a stable, unique `bay_id` within the image.
Other fields remain flexible.
An empty bay has `vehicle_present: false`.
An omitted bay has not been labeled.

```json
{
  "vehicles": {
    "bays": [
      {
        "bay_id": "bay_1",
        "vehicle_present": true,
        "vehicle_type": "sedan",
        "color": "white"
      },
      {
        "bay_id": "bay_2",
        "vehicle_present": false
      }
    ]
  },
  "model": "classifier-v1"
}
```

The database enforces valid JSON with an object at the root.
It does not enforce the metadata convention or require specific fields.
SQLite JSON functions, such as `json_extract`, can query these fields.
See the [SQLite JSON documentation](https://www.sqlite.org/json1.html).

## Create the database

The tracked database already contains this migration.
Run this command only when creating a new database from the repository root:

```sh
sqlite3 -bail db/labels.sqlite3 < db/migrations/001_image_labels.sql
```

The migration creates the table and sets `PRAGMA user_version` to `1` in one transaction.
The command fails if the table already exists.
Apply each numbered migration once, in order.
There is no migration runner or application input interface yet.

Check the database version and integrity:

```sh
sqlite3 db/labels.sqlite3 'PRAGMA user_version; PRAGMA integrity_check;'
```
