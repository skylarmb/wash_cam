# Dashboard

Run `uv run wash-cam dashboard` and open http://127.0.0.1:8000 on the same computer. Press Ctrl+C to stop the server.

The page reloads every 30 seconds. It shows the JPEG with the latest timestamp filename from the checkout's `tmp/images/` directory. The same image stays visible until a newer capture appears. A missing or empty directory shows `No images yet`.

The standard-library WSGI server serves the page at `/` and the latest image at `/latest.jpg`. Responses disable browser caching. Other routes return 404. The dashboard does not capture images or require new dependencies.
