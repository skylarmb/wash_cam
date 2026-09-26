"""Serve the latest camera capture on a local web page."""

from wsgiref import simple_server

from wash_cam.camera import capture


def application(environ, start_response):
    """Serve the page or the latest JPEG without browser caching."""
    status, content_type, body = (
        "404 Not Found",
        "text/plain; charset=utf-8",
        b"Not found",
    )
    path = environ.get("PATH_INFO", "/")
    if path in ("/", "/latest.jpg"):
        # Timestamp filenames sort in capture order, even if file times change.
        latest = max(
            (image for image in capture.IMAGES_DIR.glob("*.jpg") if image.is_file()),
            key=lambda image: image.name,
            default=None,
        )
        if path == "/":
            content = (
                '<img src="/latest.jpg" alt="Latest wash cam capture">'
                if latest is not None
                else "<p>No images yet</p>"
            )
            body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>Wash Cam</title>
<style>body {{ font-family: sans-serif; margin: 1rem; }}
img {{ max-width: 100%; height: auto; }}</style>
</head>
<body><h1>Wash Cam</h1>{content}</body>
</html>
""".encode()
            status, content_type = "200 OK", "text/html; charset=utf-8"
        elif latest is not None:
            try:
                body = latest.read_bytes()
            except FileNotFoundError:
                # A removed capture can be replaced on the next page refresh.
                pass
            else:
                status, content_type = "200 OK", "image/jpeg"

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
    """Serve the dashboard until the user presses Ctrl+C."""
    with simple_server.make_server("127.0.0.1", 8000, application) as server:
        print("Dashboard: http://127.0.0.1:8000", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
