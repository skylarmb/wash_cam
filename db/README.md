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
Updates must set `verified_at` to the new verification time.
SQLite does not check whether the image file exists.

## Metadata convention

Use `vehicles.bays` for an array of bay entries.
Give each entry a stable, unique `bay_id` within the image.
Other fields remain flexible.
The labeling interface saves `vehicle_count` for bays `bay_0` through `bay_4`.
A count of zero means the bay is empty.
An omitted bay has not been labeled.

```json
{
  "vehicles": {
    "bays": [
      {
        "bay_id": "bay_0",
        "vehicle_count": 2
      },
      {
        "bay_id": "bay_1",
        "vehicle_count": 0
      },
      {"bay_id": "bay_2", "vehicle_count": 1},
      {"bay_id": "bay_3", "vehicle_count": 0},
      {"bay_id": "bay_4", "vehicle_count": 0}
    ]
  }
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
There is no migration runner.

Check the database version and integrity:

```sh
sqlite3 db/labels.sqlite3 'PRAGMA user_version; PRAGMA integrity_check;'
```

## Labeling API

The dashboard serves `GET /api/labels?image_path=<filename>`.
It returns `null` for an unverified image or an object with `metadata` and `verified_at`.
Unknown images return HTTP 404.
The dashboard lists JPEG files directly inside `images/`.

Send `POST /api/labels` with `Content-Type: application/json`:

```json
{"image_path": "capture.jpg", "counts": [2, 0, 1, 0, 0]}
```

The five counts correspond to bays 0 through 4.
Each count must be a nonnegative integer.
A successful request returns `{"saved": true}`.
The API replaces the image's metadata with these counts and sets the current UTC verification time.
Invalid counts return HTTP 400. Database failures return HTTP 503.
Only images without a database record inherit the last counts saved in this browser tab.
Any stored metadata object blocks inheritance, including `{}` or null bay fields.
Missing or invalid counts in stored metadata default to zero.
The schema prohibits a null metadata column. An absent row is the API’s `null` state.
A new browser tab, invalid session data, or unavailable session storage uses zero defaults.
Inherited counts are suggestions until the user saves the image.
Previous image and Next image save all five counts before navigation.
Enter and the save button save the current image and open the next image. The last image wraps to the first.
Bay 0 receives focus on load. Space increments the focused count and advances focus. Tab preserves the count.
A failed save keeps the current image open.
No schema migration is required for these metadata fields.
