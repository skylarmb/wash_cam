"""Serve camera captures and save verified vehicle counts."""

import json
import sqlite3
from contextlib import closing
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, urlencode

from werkzeug import serving

from wash_cam.config import BAYS

# Resolve shared files from the checkout, regardless of the working directory.
ROOT = Path(__file__).resolve().parents[3]
# Read camera output from the directory that Git ignores.
IMAGES_DIR = ROOT / "tmp" / "images"
DATABASE_PATH = ROOT / "db" / "labels.sqlite3"
# Input titles for each bay ID prefix: wash bays and the vacuum station.
BAY_TITLES = {"bay": "Bay", "vac": "Vacuum"}


def image_names():
    """List captures in timestamp order and exclude external symlinks."""
    return sorted(
        image.name
        for image in IMAGES_DIR.glob("*.jpg")
        if image.is_file() and image.resolve().parent == IMAGES_DIR.resolve()
    )


def read_label(name):
    """Read the current verified record without creating a missing database."""
    with closing(sqlite3.connect(f"{DATABASE_PATH.as_uri()}?mode=rw", uri=True)) as db:
        row = db.execute(
            "SELECT metadata, verified_at FROM image_labels WHERE image_path = ?",
            (name,),
        ).fetchone()
    return {"metadata": json.loads(row[0]), "verified_at": row[1]} if row else None


def bay_title(bay_id):
    """Turn a bay ID such as `vac_0` into an input title such as `Vacuum 0`."""
    prefix, _, number = bay_id.partition("_")
    return f"{BAY_TITLES[prefix]} {number}"


def valid_counts(counts):
    """Accept one nonnegative whole-number count for exactly the configured bays."""
    return (
        isinstance(counts, dict)
        and set(counts) == set(BAYS)
        and all(type(count) is int and count >= 0 for count in counts.values())
    )


def save_label(name, counts):
    """Replace every bay count and update the verification time atomically."""
    metadata = {
        "vehicles": {
            "bays": [
                {"bay_id": bay_id, "vehicle_count": counts[bay_id]} for bay_id in BAYS
            ]
        }
    }
    with (
        closing(sqlite3.connect(f"{DATABASE_PATH.as_uri()}?mode=rw", uri=True)) as db,
        db,
    ):
        db.execute(
            "INSERT INTO image_labels (image_path, metadata) VALUES (?, ?) "
            "ON CONFLICT(image_path) DO UPDATE SET metadata = excluded.metadata, "
            "verified_at = CURRENT_TIMESTAMP",
            (name, json.dumps(metadata)),
        )


def page(content, labeling=False):
    """Render navigation tabs and refresh only the live page."""
    refresh = "" if labeling else '<meta http-equiv="refresh" content="30">'
    live_current = "" if labeling else ' aria-current="page"'
    labels_current = ' aria-current="page"' if labeling else ""
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{refresh}<title>Wash Cam</title>
<style>
body {{ font-family: sans-serif; margin: 1rem auto; padding: 0 1rem; max-width: 1000px; }}
img {{ max-width: 100%; height: auto; }}
nav {{ display: flex; gap: 1rem; border-bottom: 1px solid #aaa; margin-bottom: 1rem; }}
nav a {{ padding: .75rem; color: #234; text-decoration: none; }}
nav a[aria-current] {{ border-bottom: 3px solid #267; font-weight: bold; }}
.bays {{ display: flex; flex-wrap: wrap; gap: 1rem; margin: 1rem 0; }}
.bays label {{ display: grid; gap: .4rem; }}
input {{ width: 6rem; }}
input, select, button {{ font: inherit; padding: .5rem; }}
select {{ max-width: 100%; }}
.image-controls {{ display: flex; flex-wrap: wrap; align-items: center; gap: .75rem; margin: 1rem 0; }}
.image-step {{ display: inline-block; padding: .65rem 1rem; border: 1px solid #267;
  border-radius: .4rem; background: #f0f7f8; color: #234; text-decoration: none; font-weight: bold; }}
.image-step:hover {{ background: #dceef0; }}
.image-step:focus-visible {{ outline: 3px solid #267; outline-offset: 3px; }}
.image-step[aria-disabled] {{ color: #777; border-color: #ccc; background: #f4f4f4; }}
</style></head><body><h1>Wash Cam</h1>
<nav aria-label="Dashboard"><a href="/"{live_current}>Live view</a>
<a href="/labeling"{labels_current}>Data labeling</a></nav>
{content}</body></html>'''.encode()


def labeling_page(names, name):
    """Fill inputs from the selected image's saved bay counts."""
    if not names:
        return page("<p>No images yet</p>", labeling=True)
    saved = read_label(name)
    # Metadata stays flexible. Missing counts default to zero.
    counts = {}
    if saved is not None:
        vehicles = saved["metadata"].get("vehicles", {})
        bays = vehicles.get("bays", []) if isinstance(vehicles, dict) else []
        for bay in bays if isinstance(bays, list) else []:
            if isinstance(bay, dict) and isinstance(bay.get("bay_id"), str):
                count = bay.get("vehicle_count")
                if type(count) is int and count >= 0:
                    counts[bay["bay_id"]] = count
    # Adjacent captures follow timestamp filename order.
    position = names.index(name)
    steps = []
    for offset, label, text in [
        (-1, "Previous image", "← Previous image"),
        (1, "Next image", "Next image →"),
    ]:
        target = (position + 1) % len(names) if offset == 1 else position - 1
        if 0 <= target < len(names):
            link = escape(urlencode({"image_path": names[target]}), quote=True)
            steps.append(
                f'<a class="image-step" aria-label="{label}" href="/labeling?{link}">{text}</a>'
            )
        else:
            steps.append(
                f'<span class="image-step" role="link" aria-label="{label}" aria-disabled="true">{text}</span>'
            )
    navigation = f'<div class="image-controls">{steps[0]}<span>Image {position + 1} of {len(names)}</span>{steps[1]}</div>'
    options = "".join(
        f'<option value="{escape(item, quote=True)}"'
        f"{' selected' if item == name else ''}>{escape(item)}</option>"
        for item in reversed(names)
    )
    inputs = "".join(
        f'<label for="{bay_id}">{bay_title(bay_id)}<input id="{bay_id}" name="{bay_id}" '
        f'type="number" min="0" step="1" required value="{counts.get(bay_id, 0)}"></label>'
        for bay_id in BAYS
    )
    query = escape(urlencode({"image_path": name}), quote=True)
    return page(
        f"""
{navigation}
<form method="get" action="/labeling">
<label for="image_path">Image</label>
<select id="image_path" name="image_path" onchange="this.form.submit()">{options}</select>
<button type="submit">Load image</button></form>
<p><img src="/image?{query}" alt="Selected capture for labeling"></p>
<form id="labels"><fieldset><legend>Vehicle count per bay</legend>
<p>Tab: next bay · Space: add one and next bay · Enter: save and next image</p>
<div class="bays">{inputs}</div>
<button id="save" type="submit">Save verified labels and next image</button></fieldset></form>
<p id="status" role="status">{"Saved labels loaded." if saved else "This image has no verified labels."}</p>
<script>
// Save the current capture before navigation. Keep it visible if saving fails.
const form = document.getElementById('labels');
const imagePath = document.getElementById('image_path').value;
let saving = false;
const nextImage = document.querySelector('a[aria-label="Next image"]');
const bayInputs = Array.from(form.querySelectorAll('input'));
// Existing metadata always wins, including empty objects and incomplete labels.
const hasSavedMetadata = {json.dumps(saved is not None)};
// Counts are keyed by bay ID. A new key ignores older positional memories.
const previousCountsKey = 'wash-cam.previous-verified-counts-by-bay';
if (!hasSavedMetadata) {{
  try {{
    const previous = JSON.parse(sessionStorage.getItem(previousCountsKey));
    if (previous && typeof previous === 'object' && !Array.isArray(previous) &&
        Object.keys(previous).length === bayInputs.length &&
        bayInputs.every(input => Number.isSafeInteger(previous[input.id]) && previous[input.id] >= 0)) {{
      bayInputs.forEach(input => {{ input.value = previous[input.id]; }});
      document.getElementById('status').textContent = 'Previous counts loaded. Verify this image before saving.';
    }}
  }} catch {{
    // Missing, invalid, or unavailable session storage leaves the zero defaults.
  }}
}}
// Keep Tab native. Space adds one vehicle and advances to the next control.
bayInputs.forEach((input, index) => {{
  input.addEventListener('keydown', event => {{
    if (event.key !== ' ' || event.ctrlKey || event.altKey || event.metaKey) return;
    event.preventDefault();
    if (saving || event.repeat) return;
    input.stepUp();
    (bayInputs[index + 1] || document.getElementById('save')).focus();
  }});
}});
bayInputs[0].focus();
async function saveLabels(destination) {{
  if (saving || !form.reportValidity()) return;
  saving = true;
  const status = document.getElementById('status');
  const button = document.getElementById('save');
  const counts = Object.fromEntries(bayInputs.map(input => [input.id, Number(input.value)]));
  button.disabled = true;
  status.textContent = 'Saving…';
  try {{
    const response = await fetch('/api/labels', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{image_path: imagePath, counts}})
    }});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Save failed.');
    // Remember only confirmed saves. Storage failures must not block navigation.
    try {{ sessionStorage.setItem(previousCountsKey, JSON.stringify(counts)); }} catch {{}}
    status.textContent = 'Verified labels saved.';
    if (destination) window.location.assign(destination);
  }} catch (error) {{
    status.textContent = error.message;
  }} finally {{
    button.disabled = false;
    saving = false;
  }}
}}
form.addEventListener('submit', async (event) => {{
  event.preventDefault();
  await saveLabels(nextImage.href);
}});
// Intercept both image links so each transition saves every bay count.
document.querySelectorAll('a.image-step').forEach(link => {{
  link.addEventListener('click', async (event) => {{
    event.preventDefault();
    await saveLabels(link.href);
  }});
}});
</script>""",
        labeling=True,
    )


def route(environ):
    """Handle page, image, and JSON requests."""
    path = environ.get("PATH_INFO", "/")
    method = environ.get("REQUEST_METHOD", "GET")
    names = image_names()
    name = parse_qs(environ.get("QUERY_STRING", "")).get("image_path", [None])[0]
    if method == "POST" and path == "/api/labels":
        if environ.get("CONTENT_TYPE", "").split(";")[0] != "application/json":
            return (
                "415 Unsupported Media Type",
                "application/json",
                b'{"error":"Use application/json."}',
            )
        try:
            size = int(environ.get("CONTENT_LENGTH", "0"))
            if not 0 < size <= 8192:
                raise ValueError
            payload = json.loads(environ["wsgi.input"].read(size))
            if not isinstance(payload, dict):
                raise TypeError
            name, counts = payload.get("image_path"), payload.get("counts")
            if not valid_counts(counts):
                raise ValueError
        except (ValueError, TypeError, UnicodeError):
            return (
                "400 Bad Request",
                "application/json",
                b'{"error":"Provide a nonnegative whole-number count for every bay."}',
            )
        if name not in names:
            return "404 Not Found", "application/json", b'{"error":"Image not found."}'
        save_label(name, counts)
        return "200 OK", "application/json", b'{"saved":true}'
    if method != "GET":
        return (
            "405 Method Not Allowed",
            "application/json",
            b'{"error":"Method not allowed."}',
        )
    if path == "/":
        content = (
            '<img src="/latest.jpg" alt="Latest wash cam capture">'
            if names
            else "<p>No images yet</p>"
        )
        return "200 OK", "text/html; charset=utf-8", page(content)
    if path == "/labeling":
        if name is not None and name not in names:
            return "404 Not Found", "text/plain", b"Image not found"
        return (
            "200 OK",
            "text/html; charset=utf-8",
            labeling_page(names, name or (names[-1] if names else None)),
        )
    if path == "/latest.jpg":
        name = names[-1] if names else None
    if path in ("/latest.jpg", "/image") and name in names:
        return "200 OK", "image/jpeg", (IMAGES_DIR / name).read_bytes()
    if path == "/api/labels" and name in names:
        return "200 OK", "application/json", json.dumps(read_label(name)).encode()
    return "404 Not Found", "application/json", b'{"error":"Not found."}'


def application(environ, start_response):
    """Serve responses without caching and report unavailable storage."""
    try:
        status, content_type, body = route(environ)
    except FileNotFoundError:
        status, content_type, body = (
            "404 Not Found",
            "application/json",
            b'{"error":"Image not found."}',
        )
    except sqlite3.Error:
        status, content_type, body = (
            "503 Service Unavailable",
            "application/json",
            b'{"error":"Label database is unavailable. Try again."}',
        )
    start_response(
        status,
        [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
        ],
    )
    return [body]


def run() -> None:
    """Serve the dashboard and restart when Python source files change."""
    print(
        "Dashboard: http://127.0.0.1:8000 (network: http://<host-ip>:8000)", flush=True
    )
    # Listen on all IPv4 interfaces so other computers can access the dashboard.
    # The development reloader runs the application in a restartable child process.
    serving.run_simple("0.0.0.0", 8000, application, use_reloader=True)
