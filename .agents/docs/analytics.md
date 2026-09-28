# Analytics tab

`/analytics` is a static shell (`ANALYTICS_PAGE` in `dashboard/__init__.py`) that loads `dashboard/static/analytics.js`, `analytics.css`, and a vendored ECharts 5.6.0 (`static/echarts.min.js`, Apache-2.0, served offline). The script fetches `GET /api/analytics?from=YYYY-MM-DD&to=YYYY-MM-DD` on load and every 15 s while the tab is visible, and keeps the range in the page URL. `analytics.parse_range` defaults to the last 7 local days, caps the end at today and the span at 90 days, and malformed or backwards dates return 400. `/static/<name>` serves only the files in `STATIC_FILES`.

`wash_cam.dashboard.analytics.build` returns the whole payload, read-only, from `image_detections` capture times and the `sessions` table. Bays, their kind (`config.bay_kind`), labels (`config.bay_title`), and prices all come from `config.py`; nothing in the page is hard-coded to five bays. Every number counts only covered time: capture runs that break at gaps longer than `MAX_GAP_INTERVALS × CAPTURE_INTERVAL`, found in SQL with window functions so long ranges read only run boundaries. Times in the payload are epoch milliseconds; local days and hours use the server's timezone.

Panels, top to bottom:
- A health line: last frame age, share of the range captured, and outages. The live strip shows each bay's open session if it saw the latest frames, with a locally ticking timer and estimate. Frames older than `MAX_GAP` mark it stale.
- KPI tiles: estimated revenue, visits, median complete wash, share of covered time with all wash bays busy, and share of complete washes at or under the minimum-charge break-even (3 min).
- The bay timeline: every session as a bar in its bay's lane, with outages hatched and a zoom that the busy-bays step chart follows.
- The busy-bays chart: how many wash bays were occupied over time, plus a strip of covered time at 0 to all bays busy. "All bays busy" is the missed-business proxy until a queue region exists.
- Revenue pace: the focus day's cumulative revenue (the range's last day) against the mean of up to 28 earlier days that are each at least half captured.
- A visit-length beeswarm: complete sessions per bay, with the minimum-charge zone shaded. The axis is capped near the 95th percentile, and longer visits show as triangles at the edge.
- Typical day: mean wash bays busy per local hour, with a p25–p75 band across days. Hours under 15 covered minutes are gaps. With 14 or more days, a toggle adds a weekday-by-hour heatmap.
- Bay balance: each wash bay's share of occupied time and of revenue, against an even split.
- Daily revenue: a 13-week calendar ending at the range's end, whatever the range. Uncaptured days are gray.

Revenue is estimated from complete (`left`) sessions only, attributed to their local start day. `price` charges wash bays `max(WASH_MINIMUM, WASH_RATE_PER_MIN × minutes)` ($3 minimum, $1/min, unrounded) and the vacuum `VACUUM_RATE_PER_MIN × minutes` ($0.25/min, a placeholder). Occupied time is an upper bound on paid time, and no owner totals exist, so every revenue figure is labeled an estimate. Colors are role tokens in `analytics.css`, with light and dark steps from the dataviz reference palette.
