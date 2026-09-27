"""Check dashboard responses without sockets or camera hardware."""

import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from wsgiref import simple_server
from wsgiref.util import setup_testing_defaults

import pytest

from wash_cam import dashboard
from wash_cam.cli import main


def test_serves_committed_images_from_any_working_directory(monkeypatch, tmp_path):
    """Serve the latest shared image from the checkout's images directory."""
    images = Path(__file__).resolve().parents[1] / "images"
    expected = max(images.glob("*.jpg")).read_bytes()
    monkeypatch.chdir(tmp_path)
    response = {}

    def start_response(status, headers):
        """Record the response status."""
        response["status"] = status

    body = b"".join(dashboard.application({"PATH_INFO": "/latest.jpg"}, start_response))

    assert response["status"] == "200 OK"
    assert body == expected


@pytest.fixture
def request_dashboard(monkeypatch, tmp_path, capsys):
    """Start the CLI with a fake server and call its WSGI application."""
    applications = []

    @contextmanager
    def make_server(host, port, app):
        """Record the application instead of opening a socket."""
        assert (host, port) == ("127.0.0.1", 8000)
        applications.append(app)
        yield SimpleNamespace(serve_forever=lambda: None)

    monkeypatch.setattr(simple_server, "make_server", make_server)
    monkeypatch.setattr(dashboard, "IMAGES_DIR", tmp_path / "images")
    monkeypatch.setattr(sys, "argv", ["wash-cam", "dashboard"])
    main()
    assert "http://127.0.0.1:8000" in capsys.readouterr().out

    def request(path):
        """Return the status, headers, and body for a GET request."""
        environ = {}
        setup_testing_defaults(environ)
        environ["PATH_INFO"] = path
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
