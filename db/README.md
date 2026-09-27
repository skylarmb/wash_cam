# Image labels

`labels.sqlite3` stores one verified label record per image in `image_labels`
and one model prediction per image in `image_detections`.
The database and the captures in `tmp/images/` live only on the server (the Mac mini).
Git tracks the migrations but ignores the database and its journal files.
Label through the server's dashboard rather than copying the database around.

| Column | Meaning |
| --- | --- |
| `image_path` | Unique, non-null path relative to `tmp/images/`. Include any subdirectories. |
| `metadata` | Non-null JSON object stored as text. Label fields can vary between records. |
| `verified_at` | Non-null UTC verification time. Defaults to the insertion time in `YYYY-MM-DD HH:MM:SS` format. |

Rows written by a labeling agent add `labeled_by` (for example `agent:StormyCliff/gpt-6`), `batch_id`, `uncertain_bays`, and `notes` to `metadata`, and are written only by `wash-cam label-import`. Rows without `labeled_by` are human labels. See `.agents/docs/labeling.md`.
Verification covers all labels for an image at once.
An absent row means no verified labels have been saved.
The table stores the current labels without revision history.
Updates must set `verified_at` to the new verification time.
SQLite does not check whether the image file exists.

## Metadata convention

Use `vehicles.bays` for an array of bay entries.
Give each entry a stable, unique `bay_id` within the image.
Other fields remain flexible.
Wash bays are `bay_0` through `bay_4`, numbered from the leftmost bay.
The vacuum station is `vac_0`.
The labeling interface saves `vehicle_count` for all six.
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
      {"bay_id": "bay_4", "vehicle_count": 0},
      {"bay_id": "vac_0", "vehicle_count": 1}
    ]
  }
}
```

The database enforces valid JSON with an object at the root.
It does not enforce the metadata convention or require specific fields.
SQLite JSON functions, such as `json_extract`, can query these fields.
See the [SQLite JSON documentation](https://www.sqlite.org/json1.html).

## Create the database

Create the database, or upgrade it to the latest schema:

```sh
uv run wash-cam migrate
```

Each migration `NNN_description.sql` sets `PRAGMA user_version` to `NNN` in its own transaction.
`migrate` applies every migration newer than the recorded version, in order, and is a no-op once current.
`001` creates `image_labels`. `002` creates `image_detections`. `003` adds `image_detections.captured_at`. `004` creates `sessions`.

Check the database version and integrity:

```sh
sqlite3 db/labels.sqlite3 'PRAGMA user_version; PRAGMA integrity_check;'
```

## Labeling API

The dashboard serves `GET /api/labels?image_path=<filename>`.
It returns `null` for an unverified image or an object with `metadata` and `verified_at`.
Unknown images return HTTP 404.
The dashboard lists JPEG files directly inside `tmp/images/`.

## Detections

`wash-cam watch` stores one `image_detections` row per capture, keyed by `image_path`.
`model` names the detector, `detected_at` is the UTC detection time, and `captured_at` is the UTC capture time.

## Sessions

`sessions` holds one row per vehicle visit to a bay, derived from `image_detections` by `wash_cam.sessions`.
It is rebuildable with `wash-cam sessions --rebuild`, so never edit rows by hand.
See `.agents/docs/sessions.md` for the columns and debouncing rules.
`metadata.vehicles.bays` uses the same convention as labels, with a count for every bay.
`metadata.vehicles.detections` lists each counted vehicle with its `bay_id` (null outside every bay), `label`, `score`, and `box` as `[left, top, right, bottom]`.
Delete rows to have the watcher detect those captures again.

Send `POST /api/labels` with `Content-Type: application/json`:

```json
{"image_path": "capture.jpg", "counts": {"bay_0": 2, "bay_1": 0, "bay_2": 1, "bay_3": 0, "bay_4": 0, "vac_0": 1}}
```

`counts` maps every bay ID to its count, with no missing or extra IDs.
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
Previous image and Next image save every count before navigation.
Enter and the save button save the current image and open the next image. The last image wraps to the first.
Bay 0 receives focus on load. Space increments the focused count and advances focus. Tab preserves the count.
A failed save keeps the current image open.
No schema migration is required for these metadata fields.
