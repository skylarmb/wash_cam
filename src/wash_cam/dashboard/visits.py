"""Visit labeling tab: pages and JSON routes over wash_cam.visit_labels.

The tab shows one bay's crop at a time and saves every event as it is marked,
so there is nothing to lose on navigation. static/visits.js drives it.
"""

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs

from wash_cam import visit_labels
from wash_cam.visit_labels import VisitLabelError

# (status, content type, body), the dashboard's response shape.
Response = tuple[str, str, bytes]

# Request bodies are small JSON objects; anything bigger is a mistake.
MAX_BODY = 8192

PAGE = """<link rel="stylesheet" href="/static/visits.css">
<div class="vl-root" id="visits">
<header class="vl-head">
  <h2>Visit labeling</h2>
  <p class="vl-task" id="task" aria-live="polite"></p>
  <button type="button" class="vl-resume" id="resume" hidden></button>
</header>
<section class="vl-labeler" id="labeler" hidden aria-label="Label one bay">
  <div class="vl-stage">
    <canvas id="frame" width="414" height="520" aria-label="Current frame of this bay"></canvas>
  </div>
  <div class="vl-side">
    <p class="vl-status" id="status" role="status"></p>
    <p class="vl-save" id="save-state" aria-live="polite" data-state="saved">All changes saved</p>
    <p class="vl-events" id="frame-events"></p>
    <div class="vl-buttons" role="group" aria-label="Mark an event on this frame">
      <button type="button" data-event="arrive"><kbd>A</kbd> Arrive</button>
      <button type="button" data-event="wash_start"><kbd>W</kbd> Wash start</button>
      <button type="button" data-event="wash_stop"><kbd>S</kbd> Stop washing</button>
      <button type="button" data-event="leave"><kbd>L</kbd> Leave</button>
      <button type="button" id="clear"><kbd>⌫</kbd> Clear frame</button>
    </div>
    <div class="vl-buttons" role="group" aria-label="Move through frames">
      <button type="button" id="prev" aria-label="Previous frame">◀</button>
      <button type="button" id="next" aria-label="Next frame">▶</button>
      <button type="button" id="prev-event"><kbd>[</kbd> Prev event</button>
      <button type="button" id="next-event"><kbd>]</kbd> Next event</button>
    </div>
    <div class="vl-buttons">
      <button type="button" class="vl-finish" id="finish"><kbd>Enter</kbd> Finish bay, next task</button>
      <button type="button" id="pause"><kbd>Esc</kbd> Pause (saved)</button>
    </div>
    <p class="vl-problems" id="problems" role="alert"></p>
    <details class="vl-help">
      <summary>Keys and rubric</summary>
      <p><kbd>←</kbd>/<kbd>→</kbd> step one frame; hold to play, add <kbd>Shift</kbd>
      to play faster. <kbd>[</kbd>/<kbd>]</kbd> jump between events. <kbd>A</kbd>,
      <kbd>W</kbd>, <kbd>S</kbd>, <kbd>L</kbd> toggle an event on this frame;
      <kbd>⌫</kbd> clears the frame. Click or drag the strip below to seek.
      Every change saves immediately, so you can pause (<kbd>Esc</kbd>) or close
      the tab at any time and resume where you stopped.</p>
      <p id="rubric"></p>
    </details>
  </div>
  <div class="vl-lane-wrap">
    <canvas class="vl-lane" id="lane" height="44" aria-label="Labeled state across the window; click to seek"></canvas>
    <div class="vl-legend"><span><i class="car"></i>car present</span>
    <span><i class="washing"></i>washing</span><span><i class="event"></i>event</span></div>
  </div>
</section>
<section class="vl-picker" aria-labelledby="picker-title">
  <h3 id="picker-title">Labeling tasks</h3>
  <form class="vl-new" id="new-range">
    <label>From <input type="datetime-local" id="range-start" required></label>
    <label>To <input type="datetime-local" id="range-end" required></label>
    <button type="submit">Open range</button>
    <span class="vl-hint">Opens one task per wash bay for that range.</span>
  </form>
  <p class="vl-problems" id="picker-error" role="alert"></p>
  <table class="vl-list">
    <thead><tr><th>Range</th><th>Bay</th><th>Labeler</th><th>Events</th><th>Status</th><th></th></tr></thead>
    <tbody id="windows"></tbody>
  </table>
</section>
</div>
<script src="/static/visits.js"></script>"""


def error(status: str, message: str) -> Response:
    """A JSON error response."""
    return status, "application/json", json.dumps({"error": message}).encode()


def ok(payload: object) -> Response:
    """A JSON success response."""
    return "200 OK", "application/json", json.dumps(payload).encode()


def read_json(environ: dict) -> dict:
    """The request's JSON object body; ValueError or TypeError if it isn't one."""
    if environ.get("CONTENT_TYPE", "").split(";")[0] != "application/json":
        raise ValueError("Use application/json.")
    size = int(environ.get("CONTENT_LENGTH") or 0)
    if not 0 < size <= MAX_BODY:
        raise ValueError("Send a small JSON object.")
    body = json.loads(environ["wsgi.input"].read(size))
    if not isinstance(body, dict):
        raise TypeError("Send a JSON object.")
    return body


def whole_number(value: object) -> int:
    """A JSON integer ID; TypeError for anything else, including booleans."""
    if type(value) is not int:
        raise TypeError("IDs are whole numbers.")
    return value


def post_windows(body: dict, database: Path) -> Response:
    """Open a labeling range: one task per wash bay."""
    start = datetime.fromisoformat(str(body.get("start")))
    end = datetime.fromisoformat(str(body.get("end")))
    return ok({"ids": visit_labels.create_windows(start, end, database)})


def post_event(body: dict, database: Path) -> Response:
    """Add or remove one event on one frame."""
    if type(body.get("present")) is not bool:
        raise TypeError("present must be true or false.")
    visit_labels.set_event(
        whole_number(body.get("window_id")),
        str(body.get("image_path")),
        str(body.get("event")),
        body["present"],
        database,
    )
    return ok({"saved": True})


def post_clear(body: dict, database: Path) -> Response:
    """Remove every event on one frame."""
    visit_labels.clear_frame(
        whole_number(body.get("window_id")), str(body.get("image_path")), database
    )
    return ok({"saved": True})


def post_complete(body: dict, database: Path) -> Response:
    """Finish or reopen a task."""
    if type(body.get("completed")) is not bool:
        raise TypeError("completed must be true or false.")
    visit_labels.set_completed(
        whole_number(body.get("window_id")), body["completed"], database
    )
    return ok({"saved": True})


def post_delete(body: dict, database: Path) -> Response:
    """Delete a task opened by mistake."""
    visit_labels.delete_window(whole_number(body.get("window_id")), database)
    return ok({"deleted": True})


POSTS: dict[str, Callable[[dict, Path], Response]] = {
    "/api/visits/windows": post_windows,
    "/api/visits/event": post_event,
    "/api/visits/clear": post_clear,
    "/api/visits/complete": post_complete,
    "/api/visits/delete": post_delete,
}


def route(
    environ: dict,
    database: Path,
    images: Path,
    page: Callable[[str, str], bytes],
) -> Response | None:
    """Answer a Visit labeling request, or None when the path isn't ours."""
    path = environ.get("PATH_INFO", "/")
    method = environ.get("REQUEST_METHOD", "GET")
    if path != "/visits" and not path.startswith("/api/visits"):
        return None
    query = parse_qs(environ.get("QUERY_STRING", ""))
    try:
        if method == "POST" and path in POSTS:
            try:
                body = read_json(environ)
            except (ValueError, TypeError, UnicodeError) as problem:
                return error("400 Bad Request", str(problem))
            return POSTS[path](body, database)
        if method != "GET":
            return error("405 Method Not Allowed", "Method not allowed.")
        if path == "/visits":
            return "200 OK", "text/html; charset=utf-8", page(PAGE, "visits")
        if path == "/api/visits/windows":
            return ok(visit_labels.list_windows(database))
        window_id = int(query.get("id", [""])[0])
        if path == "/api/visits/window":
            return ok(visit_labels.window_detail(window_id, database))
        if path == "/api/visits/crop":
            image_path = query.get("image_path", [""])[0]
            return (
                "200 OK",
                "image/jpeg",
                visit_labels.window_crop(window_id, image_path, images, database),
            )
    except VisitLabelError as problem:
        return error("400 Bad Request", str(problem))
    except (ValueError, TypeError) as problem:
        return error("400 Bad Request", str(problem) or "Invalid request.")
    return error("404 Not Found", "Not found.")
