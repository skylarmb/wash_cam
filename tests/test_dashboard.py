"""Check dashboard responses without sockets or camera hardware."""

import io
import json
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from wsgiref.util import setup_testing_defaults

import cv2
import numpy as np
import pytest
from werkzeug import serving

from wash_cam import config, dashboard, db, evaluate, labeling
from wash_cam.cli import main
from wash_cam.config import BAYS


def all_bays(count):
    """Return the same count for every region, keyed by bay ID."""
    return dict.fromkeys(BAYS, count)


def test_serves_captures_from_the_checkout_capture_directory(monkeypatch, tmp_path):
    """Serve captures from the checkout's tmp/images, whatever the working directory."""
    monkeypatch.chdir(tmp_path)
    checkout = Path(__file__).resolve().parents[1]

    assert dashboard.IMAGES_DIR == checkout / "tmp" / "images"
    assert dashboard.IMAGES_DIR == config.IMAGES_DIR


@pytest.fixture
def request_dashboard(monkeypatch, tmp_path, capsys):
    """Start the CLI with a fake server and call its WSGI application."""
    applications = []
    database = tmp_path / "labels.sqlite3"
    # Build the full schema, as `wash-cam migrate` does on the server.
    db.migrate(database)
    monkeypatch.setattr(dashboard, "DATABASE_PATH", database, raising=False)

    def run_simple(host, port, app, **options):
        """Record the application instead of opening a socket."""
        assert (host, port) == ("0.0.0.0", 8000)
        applications.append(app)
        assert options["use_reloader"] is True

    monkeypatch.setattr(serving, "run_simple", run_simple)
    monkeypatch.setattr(dashboard, "IMAGES_DIR", tmp_path / "images")
    monkeypatch.setattr(sys, "argv", ["wash-cam", "dashboard"])
    main()
    assert "http://127.0.0.1:8000" in capsys.readouterr().out

    def request(path, method="GET", payload=None):
        """Return the status, headers, and body for a GET request."""
        environ = {}
        setup_testing_defaults(environ)
        url = urlsplit(path)
        environ["PATH_INFO"] = url.path
        environ["QUERY_STRING"] = url.query
        environ["REQUEST_METHOD"] = method
        data = json.dumps(payload).encode() if payload is not None else b""
        environ["wsgi.input"] = io.BytesIO(data)
        environ["CONTENT_LENGTH"] = str(len(data))
        environ["CONTENT_TYPE"] = "application/json"
        response = {}

        def start_response(status, headers):
            """Record the response metadata."""
            response.update(status=status, headers=dict(headers))

        body = b"".join(applications[0](environ, start_response))
        return response["status"], response["headers"], body

    return request


def test_page_refreshes_every_30_seconds(request_dashboard):
    """Show the latest image and disable page caching."""
    dashboard.IMAGES_DIR.mkdir()
    (dashboard.IMAGES_DIR / "20260926_162101_757377.jpg").write_bytes(b"photo")

    status, headers, body = request_dashboard("/")

    assert status == "200 OK"
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert headers["Cache-Control"] == "no-store"
    assert b'<meta http-equiv="refresh" content="30">' in body
    assert b'src="/latest.jpg"' in body


def test_latest_capture_changes_only_when_new_image_arrives(request_dashboard):
    """Use filename order and keep the same image between captures."""
    dashboard.IMAGES_DIR.mkdir()
    # Create the older capture last so modification order cannot select it.
    (dashboard.IMAGES_DIR / "20260926_162101_757377.jpg").write_bytes(b"latest")
    (dashboard.IMAGES_DIR / "20260925_162101_757377.jpg").write_bytes(b"older")
    (dashboard.IMAGES_DIR / "notes.txt").write_text("ignore")

    for _ in range(2):
        status, headers, body = request_dashboard("/latest.jpg")
        assert status == "200 OK"
        assert headers["Content-Type"] == "image/jpeg"
        assert headers["Cache-Control"] == "no-store"
        assert body == b"latest"

    (dashboard.IMAGES_DIR / "20260926_162102_000000.jpg").write_bytes(b"new")
    assert request_dashboard("/latest.jpg")[2] == b"new"


@pytest.mark.parametrize("directory_exists", [False, True])
def test_no_images_then_first_capture(request_dashboard, directory_exists):
    """Refresh an empty page until the first capture appears."""
    if directory_exists:
        dashboard.IMAGES_DIR.mkdir()

    status, _, body = request_dashboard("/")
    assert status == "200 OK"
    assert b"No images yet" in body
    assert b'content="30"' in body
    assert b"<img" not in body
    assert request_dashboard("/latest.jpg")[0] == "404 Not Found"

    dashboard.IMAGES_DIR.mkdir(exist_ok=True)
    (dashboard.IMAGES_DIR / "20260926_162101_757377.jpg").write_bytes(b"first")
    assert b'src="/latest.jpg"' in request_dashboard("/")[2]
    assert request_dashboard("/latest.jpg")[2] == b"first"


@pytest.mark.parametrize("path", ["/missing", "/../config.yaml"])
def test_unknown_routes_return_404(request_dashboard, path):
    """Do not serve other files from the checkout."""
    assert request_dashboard(path)[0] == "404 Not Found"


def test_labeling_loads_saved_counts_and_updates_one_image(request_dashboard):
    """Save all bays, load their inputs, and replace the verified record."""
    dashboard.IMAGES_DIR.mkdir()
    (dashboard.IMAGES_DIR / "capture.jpg").write_bytes(b"selected photo")
    payload = {
        "image_path": "capture.jpg",
        "counts": dict(zip(BAYS, [2, 0, 1, 0, 3, 1])),
    }
    assert request_dashboard("/api/labels?image_path=capture.jpg")[2] == b"null"
    assert request_dashboard("/api/labels", "POST", payload)[0] == "200 OK"
    status, _, body = request_dashboard("/labeling?image_path=capture.jpg")
    assert status == "200 OK"
    assert b'http-equiv="refresh"' not in body
    for bay, count in payload["counts"].items():
        assert (
            f'id="{bay}" name="{bay}" type="number" min="0" step="1" required value="{count}"'.encode()
            in body
        )
    assert b"Vacuum 0" in body
    assert request_dashboard("/image?image_path=capture.jpg")[2] == b"selected photo"
    with sqlite3.connect(dashboard.DATABASE_PATH) as connection:
        connection.execute(
            "UPDATE image_labels SET verified_at = '2000-01-01 00:00:00'"
        )
    payload["counts"] = dict(zip(BAYS, [0, 1, 2, 3, 4, 5]))
    assert request_dashboard("/api/labels", "POST", payload)[0] == "200 OK"
    saved = json.loads(request_dashboard("/api/labels?image_path=capture.jpg")[2])
    assert saved["metadata"] == {
        "vehicles": {
            "bays": [
                {"bay_id": bay, "vehicle_count": count}
                for bay, count in payload["counts"].items()
            ]
        }
    }
    assert saved["verified_at"] > "2000-01-01 00:00:00"
    with sqlite3.connect(dashboard.DATABASE_PATH) as connection:
        assert (
            connection.execute("SELECT count(*) FROM image_labels").fetchone()[0] == 1
        )


@pytest.mark.parametrize(
    "counts",
    [
        {bay: 0 for bay in list(BAYS)[:-1]},
        {**all_bays(0), "bay_9": 0},
        {**all_bays(0), "bay_0": -1},
        {**all_bays(0), "bay_0": True},
        {**all_bays(0), "bay_0": 1.5},
        {**all_bays(0), "bay_0": "1"},
        [0] * len(BAYS),
        None,
    ],
)
def test_rejects_invalid_counts(request_dashboard, counts):
    """Reject incomplete or invalid counts without writing a record."""
    dashboard.IMAGES_DIR.mkdir()
    (dashboard.IMAGES_DIR / "capture.jpg").write_bytes(b"photo")
    assert (
        request_dashboard(
            "/api/labels", "POST", {"image_path": "capture.jpg", "counts": counts}
        )[0]
        == "400 Bad Request"
    )
    with sqlite3.connect(dashboard.DATABASE_PATH) as connection:
        assert (
            connection.execute("SELECT count(*) FROM image_labels").fetchone()[0] == 0
        )


@pytest.mark.parametrize("name", ["missing.jpg", "../secret.jpg", "/tmp/secret.jpg"])
def test_rejects_unknown_images(request_dashboard, name):
    """Restrict labels and image reads to available captures."""
    assert (
        request_dashboard(
            "/api/labels", "POST", {"image_path": name, "counts": all_bays(0)}
        )[0]
        == "404 Not Found"
    )
    assert request_dashboard(f"/image?image_path={name}")[0] == "404 Not Found"


def test_labeling_empty_and_unlabeled_states(request_dashboard):
    """Show an empty state and default unlabeled counts to zero."""
    assert b"No images yet" in request_dashboard("/labeling")[2]
    dashboard.IMAGES_DIR.mkdir()
    (dashboard.IMAGES_DIR / "capture.jpg").write_bytes(b"photo")
    body = request_dashboard("/labeling")[2]
    assert body.count(b'required value="0"') == len(BAYS)
    assert b"Save verified labels" in body
    assert b"Data labeling" in request_dashboard("/")[2]


@pytest.mark.parametrize("payload", [[], "invalid", {"counts": all_bays(0)}])
def test_rejects_invalid_payload(request_dashboard, payload):
    """Return a client error for invalid request objects or missing images."""
    status, _, _ = request_dashboard("/api/labels", "POST", payload)
    assert status in ("400 Bad Request", "404 Not Found")


def test_selected_image_has_its_own_values(request_dashboard):
    """Keep saved counts separate when the user switches captures."""
    dashboard.IMAGES_DIR.mkdir()
    for name, count in [("first.jpg", 2), ("second.jpg", 4)]:
        (dashboard.IMAGES_DIR / name).write_bytes(b"photo")
        request_dashboard(
            "/api/labels", "POST", {"image_path": name, "counts": all_bays(count)}
        )
    for name, count in [("first.jpg", 2), ("second.jpg", 4)]:
        body = request_dashboard(f"/labeling?image_path={name}")[2]
        assert body.count(f'required value="{count}"'.encode()) == len(BAYS)
        assert f"/image?image_path={name}".encode() in body


def test_missing_database_returns_error_without_creating_file(request_dashboard):
    """Report unavailable storage without creating an empty database."""
    dashboard.IMAGES_DIR.mkdir()
    (dashboard.IMAGES_DIR / "capture.jpg").write_bytes(b"photo")
    dashboard.DATABASE_PATH.unlink()
    status, _, body = request_dashboard(
        "/api/labels", "POST", {"image_path": "capture.jpg", "counts": all_bays(0)}
    )
    assert status == "503 Service Unavailable"
    assert "unavailable" in json.loads(body)["error"]
    assert not dashboard.DATABASE_PATH.exists()


def test_labeling_previous_and_next_images(request_dashboard):
    """Navigate in capture order and wrap the last image to the first."""
    dashboard.IMAGES_DIR.mkdir()
    for name in ("first.jpg", "middle.jpg", "last.jpg"):
        (dashboard.IMAGES_DIR / name).write_bytes(b"photo")
    # Filename order places last.jpg between first.jpg and middle.jpg.
    body = request_dashboard("/labeling?image_path=last.jpg")[2]
    assert b'href="/labeling?image_path=first.jpg"' in body
    assert b'href="/labeling?image_path=middle.jpg"' in body
    assert b'aria-label="Previous image"' in body
    assert b'aria-label="Next image"' in body
    first = request_dashboard("/labeling?image_path=first.jpg")[2]
    assert b'aria-label="Previous image" aria-disabled="true"' in first
    last = request_dashboard("/labeling?image_path=middle.jpg")[2]
    assert b'aria-label="Next image" href="/labeling?image_path=first.jpg"' in last
    assert b"Image 2 of 3" in body


def run_navigation_script(request_dashboard, *args, saved=True):
    """Render the labeling page for one capture and run its script under Node."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the dashboard script test")
    dashboard.IMAGES_DIR.mkdir()
    (dashboard.IMAGES_DIR / "capture.jpg").write_bytes(b"photo")
    if saved:
        counts = {bay_id: 0 for bay_id in BAYS}
        payload = {"image_path": "capture.jpg", "counts": counts}
        assert request_dashboard("/api/labels", "POST", payload)[0] == "200 OK"
    html = request_dashboard("/labeling")[2].decode()
    script = html.split("<script>")[1].split("</script>")[0]
    result = subprocess.run(
        [node, str(Path(__file__).with_name("dashboard_navigation.cjs")), *args],
        check=False,
        input=script,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("direction", [0, 1, "keyboard", "last_space"])
@pytest.mark.parametrize("outcome", ["success", "error", "invalid"])
def test_navigation_saves_before_leaving(request_dashboard, direction, outcome):
    """Run the page script and check save ordering and failed-save behavior."""
    run_navigation_script(request_dashboard, str(direction), outcome)


@pytest.mark.parametrize("unlabeled", ["untouched", "edited"])
def test_navigation_saves_unlabeled_frames_only_when_edited(
    request_dashboard, unlabeled
):
    """Browsing past an unlabeled frame creates no row; editing it still saves."""
    run_navigation_script(request_dashboard, "1", "success", unlabeled, saved=False)


def test_dashboard_serves_network_with_reload(monkeypatch):
    """Listen on all IPv4 interfaces with reload and no interactive debugger."""
    calls = []

    def run_simple(host, port, app, **options):
        """Record server settings without opening a socket or starting a child."""
        calls.append((host, port, app, options))

    monkeypatch.setattr(serving, "run_simple", run_simple)
    monkeypatch.setattr(sys, "argv", ["wash-cam", "dashboard"])
    main()

    assert len(calls) == 1
    host, port, app, options = calls[0]
    assert (host, port, app) == ("0.0.0.0", 8000, dashboard.application)
    assert options["use_reloader"] is True
    assert options.get("use_debugger", False) is False


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (None, [2, 0, 1, 0, 3, 1]),
        ({}, [0] * 6),
        ({"vehicles": None}, [0] * 6),
        ({"vehicles": {"bays": [{"bay_id": "bay_0", "vehicle_count": None}]}}, [0] * 6),
        ({"vehicles": {"bays": [{"bay_id": "bay_0", "vehicle_count": 0}]}}, [0] * 6),
        (
            {"vehicles": {"bays": [{"bay_id": "vac_0", "vehicle_count": 4}]}},
            [0, 0, 0, 0, 0, 4],
        ),
    ],
)
def test_inherits_counts_only_without_a_saved_record(
    request_dashboard, metadata, expected
):
    """Preserve every stored object, including empty or incomplete metadata."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the dashboard script test")
    dashboard.IMAGES_DIR.mkdir()
    (dashboard.IMAGES_DIR / "capture.jpg").write_bytes(b"photo")
    if metadata is not None:
        with sqlite3.connect(dashboard.DATABASE_PATH) as db:
            db.execute(
                "INSERT INTO image_labels (image_path, metadata) VALUES (?, ?)",
                ("capture.jpg", json.dumps(metadata)),
            )
    html = request_dashboard("/labeling")[2].decode()
    result = subprocess.run(
        [node, str(Path(__file__).with_name("dashboard_defaults.cjs"))],
        input=json.dumps({"html": html, "expected": expected}),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_dashboard_save_keeps_agent_provenance(request_dashboard, tmp_path):
    """Saving counts updates only vehicles; agent provenance survives, so the row stays an agent label."""
    dashboard.IMAGES_DIR.mkdir()
    (dashboard.IMAGES_DIR / "capture.jpg").write_bytes(b"photo")
    batch = tmp_path / "batch"
    batch.mkdir()
    labels = batch / "labels.jsonl"
    labels.write_text(
        json.dumps(
            {
                "image_path": "capture.jpg",
                "counts": {"bay_0": 1},
                "labeled_by": "agent:X/y",
                "uncertain_bays": ["bay_0"],
                "notes": "foam",
            }
        )
        + "\n"
    )
    labeling.import_labels(labels, dashboard.IMAGES_DIR, dashboard.DATABASE_PATH)

    # Navigation in the labeling tab triggers exactly this save.
    status = request_dashboard(
        "/api/labels", "POST", {"image_path": "capture.jpg", "counts": all_bays(2)}
    )[0]

    assert status == "200 OK"
    with sqlite3.connect(dashboard.DATABASE_PATH) as connection:
        (stored,) = connection.execute("SELECT metadata FROM image_labels").fetchone()
    metadata = json.loads(stored)
    assert metadata["labeled_by"] == "agent:X/y"
    assert metadata["uncertain_bays"] == ["bay_0"]
    assert metadata["notes"] == "foam"
    assert {bay["vehicle_count"] for bay in metadata["vehicles"]["bays"]} == {2}
    assert "capture.jpg" in evaluate.load_labels(dashboard.DATABASE_PATH, "agent")
    # The agent can still replace its own row.
    result = labeling.import_labels(
        labels, dashboard.IMAGES_DIR, dashboard.DATABASE_PATH
    )
    assert result.imported == 1


def test_analytics_tab_serves_its_shell_without_refresh(request_dashboard):
    """The tab is a static shell; its script polls the API instead of reloading."""
    status, _, body = request_dashboard("/analytics")

    assert status == "200 OK"
    assert b"<h2>Analytics</h2>" in body
    assert b'<a href="/analytics" aria-current="page">Analytics</a>' in body
    assert b'http-equiv="refresh"' not in body
    assert b'<script src="/static/analytics.js">' in body


@pytest.mark.parametrize("query", ["", "?from=2026-09-01&to=2026-09-05"])
def test_analytics_api_returns_the_payload(request_dashboard, query):
    """The API answers with JSON for the default and an explicit range."""
    status, headers, body = request_dashboard(f"/api/analytics{query}")

    assert status == "200 OK"
    assert headers["Content-Type"] == "application/json"
    payload = json.loads(body)
    assert {"meta", "live", "summary", "sessions", "daily"} <= payload.keys()


@pytest.mark.parametrize(
    "query", ["?from=bad", "?from=2026-09-05&to=2026-09-01", "?to=2026-13-01"]
)
def test_analytics_api_rejects_bad_ranges(request_dashboard, query):
    """Malformed or backwards dates are a client error."""
    status, _, body = request_dashboard(f"/api/analytics{query}")

    assert status == "400 Bad Request"
    assert b"YYYY-MM-DD" in body


def test_static_files_are_allowlisted(request_dashboard):
    """Only the listed static files are served, with their content types."""
    status, headers, _ = request_dashboard("/static/analytics.js")
    assert status == "200 OK"
    assert headers["Content-Type"].startswith("text/javascript")

    for path in ["/static/missing.js", "/static/../__init__.py"]:
        assert request_dashboard(path)[0] == "404 Not Found"


def add_capture(database, images, name, captured_at):
    """Store one capture image and its detection time for visit labeling."""
    images.mkdir(exist_ok=True)
    cv2.imwrite(str(images / name), np.zeros((340, 1270, 3), dtype=np.uint8))
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "INSERT INTO image_detections (image_path, metadata, model, captured_at) "
            "VALUES (?, '{}', 'test', ?)",
            (name, captured_at),
        )


def test_visit_labeling_tab_is_a_nav_tab(request_dashboard):
    """The tab serves its shell and marks itself current."""
    status, _, body = request_dashboard("/visits")

    assert status == "200 OK"
    assert b'<a href="/visits" aria-current="page">Visit labeling</a>' in body
    assert b'<script src="/static/visits.js">' in body
    # Labels save as they are marked, so the page offers pause and resume.
    assert b'id="pause"' in body
    assert b'id="resume"' in body
    assert b'id="save-state"' in body


def test_visit_labeling_round_trip(request_dashboard, tmp_path):
    """Open a range, mark an event, and read it back with the bay's crop."""
    database = dashboard.DATABASE_PATH
    add_capture(database, dashboard.IMAGES_DIR, "a.jpg", "2026-09-27 03:00:00.000")
    add_capture(database, dashboard.IMAGES_DIR, "b.jpg", "2026-09-27 03:00:15.000")

    # The dashboard sends local wall-clock times, whatever the test's timezone.
    first = datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc).astimezone()
    start, end = first - timedelta(minutes=1), first + timedelta(minutes=1)
    status, _, body = request_dashboard(
        "/api/visits/windows",
        "POST",
        {"start": f"{start:%Y-%m-%dT%H:%M}", "end": f"{end:%Y-%m-%dT%H:%M}"},
    )
    assert status == "200 OK", body
    window = json.loads(body)["ids"][0]
    status, _, _ = request_dashboard(
        "/api/visits/event",
        "POST",
        {
            "window_id": window,
            "image_path": "b.jpg",
            "event": "arrive",
            "present": True,
        },
    )
    assert status == "200 OK"

    status, _, body = request_dashboard(f"/api/visits/window?id={window}")
    detail = json.loads(body)
    assert status == "200 OK"
    assert detail["events"] == {"b.jpg": ["arrive"]}
    assert [frame["image_path"] for frame in detail["frames"]] == ["a.jpg", "b.jpg"]
    status, headers, body = request_dashboard(
        f"/api/visits/crop?id={window}&image_path=a.jpg"
    )
    assert status == "200 OK"
    assert headers["Content-Type"] == "image/jpeg"
    assert body[:2] == b"\xff\xd8"


@pytest.mark.parametrize(
    ("path", "method", "payload"),
    [
        ("/api/visits/window?id=abc", "GET", None),
        ("/api/visits/window?id=99", "GET", None),
        ("/api/visits/crop?id=99&image_path=a.jpg", "GET", None),
        ("/api/visits/windows", "POST", {"start": "soon", "end": "later"}),
        ("/api/visits/event", "POST", {"window_id": True, "event": "arrive"}),
        ("/api/visits/event", "POST", ["not", "an", "object"]),
    ],
)
def test_visit_labeling_rejects_bad_requests(request_dashboard, path, method, payload):
    """Malformed IDs, times, and bodies are client errors, never crashes."""
    status, _, body = request_dashboard(path, method, payload)

    assert status == "400 Bad Request", body
    assert "error" in json.loads(body)
