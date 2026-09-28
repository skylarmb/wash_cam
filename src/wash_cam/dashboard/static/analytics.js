// Analytics tab: fetch /api/analytics, draw every panel, and poll for updates.
// All bays, prices, and ranges come from the payload; nothing is hard-coded
// here except presentation choices.
(() => {
  "use strict";

  // Captures arrive every 15 s, so polling faster shows nothing new.
  const POLL_MS = 15000;
  // Show the weekday heatmap toggle once hours have this many days behind them.
  const WEEKDAY_MIN_DAYS = 14;
  // Default timeline window when a range holds more history than fits.
  const TIMELINE_DEFAULT_MS = 2 * 24 * 3600 * 1000;
  // Charts that share a time axis zoom together.
  const TIME_GROUP = "washcam-time";

  const root = document.getElementById("analytics");
  const charts = {};
  const state = {
    // Inclusive local dates; null lets the server pick its default week.
    start: null,
    end: null,
    payload: null,
    // Server clock minus browser clock, so live timers match the server.
    clockSkewMs: 0,
    pollTimer: null,
    // The timeline's zoom window, kept across polls.
    zoom: null,
    hourlyView: "profile",
    // Bumped per request so a slow response never overwrites a newer one.
    requestId: 0,
  };

  // ---------- formatting ----------

  const dollars = (value, digits = 0) =>
    `$${value.toLocaleString(undefined, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    })}`;
  const percent = (share) => `${Math.round(share * 100)}%`;
  const pad = (value) => String(value).padStart(2, "0");

  /** Minutes and seconds (or hours and minutes) for a duration in seconds. */
  function clock(seconds) {
    const whole = Math.max(0, Math.round(seconds));
    const hours = Math.floor(whole / 3600);
    const minutes = Math.floor((whole % 3600) / 60);
    return hours
      ? `${hours}h ${pad(minutes)}m`
      : `${minutes}:${pad(whole % 60)}`;
  }

  /** A duration in seconds as a short phrase such as "7.4 min". */
  function minutesText(seconds) {
    if (seconds == null) return "–";
    const minutes = seconds / 60;
    return minutes >= 90
      ? `${(minutes / 60).toFixed(1)} h`
      : `${minutes.toFixed(1)} min`;
  }

  function timeOfDay(ms) {
    const moment = new Date(ms);
    return `${pad(moment.getHours())}:${pad(moment.getMinutes())}`;
  }

  function dateTime(ms) {
    return new Date(ms).toLocaleString(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  /** "HH:MM" for a minute of the day. */
  function minuteLabel(minute) {
    return `${pad(Math.floor(minute / 60) % 24)}:${pad(minute % 60)}`;
  }

  /** A local date as the API's YYYY-MM-DD. */
  function isoDate(day) {
    return `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`;
  }

  function addDays(text, days) {
    const [year, month, day] = text.split("-").map(Number);
    return isoDate(new Date(year, month - 1, day + days));
  }

  /** The most a visit of this length could pay, mirroring analytics.price(). */
  function visitPrice(pricing, kind, seconds) {
    if (kind === "vacuum") return (pricing.vacuum_rate_per_min * seconds) / 60;
    // The allowance matches the server: float error never buys an extra token.
    const needed = Math.ceil(seconds / 60 / pricing.token_minutes - 1e-6);
    return Math.max(pricing.wash_start_tokens, needed) * pricing.token_price;
  }

  // ---------- theme ----------

  /** Read the role tokens from CSS so charts follow light and dark mode. */
  function palette() {
    const style = getComputedStyle(root);
    const token = (name) => style.getPropertyValue(`--${name}`).trim();
    return {
      surface: token("surface-1"),
      text: token("text-primary"),
      text2: token("text-secondary"),
      muted: token("text-muted"),
      grid: token("grid"),
      axis: token("axis"),
      border: token("border"),
      wash: token("wash"),
      vacuum: token("vacuum"),
      revenue: token("revenue"),
      baseline: token("baseline"),
      outage: token("outage"),
      ramp: [0, 1, 2, 3, 4, 5].map((step) => token(`ramp-${step}`)),
    };
  }

  /** Shared axis, grid, and tooltip styling for every chart. */
  function chrome(colors) {
    const axis = {
      axisLine: { lineStyle: { color: colors.axis } },
      axisTick: { show: false },
      axisLabel: { color: colors.muted, fontSize: 11 },
      splitLine: { lineStyle: { color: colors.grid } },
      nameTextStyle: { color: colors.muted, fontSize: 11 },
    };
    return {
      axis,
      tooltip: {
        backgroundColor: colors.surface,
        borderColor: colors.border,
        textStyle: { color: colors.text, fontSize: 12 },
        extraCssText: "box-shadow: 0 4px 16px rgba(0,0,0,.12); border-radius: 8px;",
      },
      textStyle: { fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif" },
    };
  }

  function bayColor(colors, kind) {
    return kind === "vacuum" ? colors.vacuum : colors.wash;
  }

  // ---------- chart plumbing ----------

  /** Create a chart once and resize it with its container. */
  function chart(id) {
    if (!charts[id]) {
      const element = document.getElementById(id);
      charts[id] = echarts.init(element, null, { renderer: "canvas" });
      new ResizeObserver(() => charts[id].resize()).observe(element);
    }
    return charts[id];
  }

  /** Show a sentence instead of the chart, or clear it when there is data. */
  function setEmpty(id, message) {
    const element = document.getElementById(id);
    if (message) {
      element.dataset.empty = message;
      chart(id).clear();
    } else {
      delete element.dataset.empty;
    }
    return Boolean(message);
  }

  /** Spans inside the range where captures were not running. */
  function outageSpans(payload) {
    const { start_ms: start, end_ms: end } = payload.meta.range;
    const spans = [];
    let cursor = start;
    for (const [runStart, runEnd] of payload.meta.coverage_runs) {
      if (runStart > cursor) spans.push([cursor, runStart]);
      cursor = Math.max(cursor, runEnd);
    }
    if (end > cursor) spans.push([cursor, end]);
    return spans;
  }

  /** A mark area that hatches outages on a time axis. */
  function outageArea(payload, colors) {
    return {
      silent: true,
      itemStyle: {
        color: colors.outage,
        decal: {
          symbol: "rect",
          symbolSize: 1,
          dashArrayX: [1, 0],
          dashArrayY: [2, 5],
          rotation: -Math.PI / 4,
          color: colors.grid,
        },
      },
      data: outageSpans(payload).map(([from, to]) => [{ xAxis: from }, { xAxis: to }]),
    };
  }

  /** Where the timeline opens: the latest data, at most two days of it. */
  function defaultZoom(payload) {
    const { start_ms: start, end_ms: end } = payload.meta.range;
    const runs = payload.meta.coverage_runs;
    const firstData = runs.length ? runs[0][0] : start;
    return [Math.max(firstData, end - TIMELINE_DEFAULT_MS, start), end];
  }

  // ---------- panels ----------

  /** Health pill: last frame age, coverage, and outages. */
  function renderHealth(payload) {
    const health = document.getElementById("health");
    const { live, summary } = payload;
    const parts = [];
    if (live.last_frame == null) {
      parts.push("No captures yet");
    } else {
      const age = (payload.meta.now - live.last_frame) / 1000;
      parts.push(
        live.stale ? `Camera offline for ${clock(age)}` : `Last frame ${Math.round(age)} s ago`,
      );
    }
    if (summary.span_s > 0) {
      parts.push(`${percent(summary.covered_s / summary.span_s)} of range captured`);
    }
    parts.push(`${summary.outages} outage${summary.outages === 1 ? "" : "s"}`);
    health.textContent = parts.join(" · ");
    health.dataset.state = live.stale ? "stale" : "ok";
  }

  /** Live strip: one card per bay, ticking locally between polls. */
  function renderLive(payload) {
    const container = document.getElementById("live");
    const colors = palette();
    container.replaceChildren(
      ...payload.meta.bays.map((bay) => {
        const card = document.createElement("div");
        card.className = "bay-card";
        card.dataset.bay = bay.id;
        card.style.setProperty("--bay-color", bayColor(colors, bay.kind));
        card.innerHTML =
          '<div class="bay-card__name"></div><div class="bay-card__state"></div>' +
          '<div class="bay-card__money"></div>';
        card.querySelector(".bay-card__name").textContent = bay.label;
        return card;
      }),
    );
    tickLive();
  }

  /** Update elapsed time and running estimate on each live card. */
  function tickLive() {
    const payload = state.payload;
    if (!payload) return;
    const now = Date.now() + state.clockSkewMs;
    const pricing = payload.meta.pricing;
    for (const bay of payload.live.bays) {
      const card = document.querySelector(`.bay-card[data-bay="${bay.bay_id}"]`);
      if (!card) continue;
      const kind = payload.meta.bays.find((item) => item.id === bay.bay_id).kind;
      const stateText = card.querySelector(".bay-card__state");
      const money = card.querySelector(".bay-card__money");
      card.dataset.occupied = String(bay.occupied);
      if (payload.live.stale) {
        stateText.textContent = "Unknown";
        money.textContent = "camera offline";
      } else if (bay.occupied) {
        const seconds = (now - bay.since) / 1000;
        stateText.textContent = clock(seconds);
        money.textContent = `up to ${dollars(visitPrice(pricing, kind, seconds), 2)} so far`;
      } else {
        stateText.textContent = "Empty";
        money.textContent = " ";
      }
    }
  }

  /** Headline numbers for the range. */
  function renderKpis(payload) {
    const { summary, meta } = payload;
    const washBays = payload.concurrency.wash_bays;
    const tiles = [
      ["Max possible revenue", dollars(summary.revenue), "all bay time, whole tokens"],
      ["Wash visits", summary.wash_visits.toLocaleString(), `${summary.vacuum_visits} vacuum`],
      ["Median wash", minutesText(summary.median_wash_s), "arrival to departure"],
      [
        "All bays busy",
        summary.full_share == null ? "–" : percent(summary.full_share),
        `all ${washBays} wash bays taken`,
      ],
      [
        "Start tokens only",
        summary.minimum_only_share == null ? "–" : percent(summary.minimum_only_share),
        `washes ≤ ${Math.round(meta.pricing.minimum_break_even_s / 60)} min`,
      ],
      ["Hours captured", (summary.covered_s / 3600).toFixed(1), `of ${(summary.span_s / 3600).toFixed(0)} h in range`],
    ];
    document.getElementById("kpis").replaceChildren(
      ...tiles.map(([label, value, hint]) => {
        const tile = document.createElement("div");
        tile.className = "kpi";
        tile.innerHTML =
          '<div class="kpi__label"></div><div class="kpi__value"></div><div class="kpi__hint"></div>';
        tile.querySelector(".kpi__label").textContent = label;
        tile.querySelector(".kpi__value").textContent = value;
        tile.querySelector(".kpi__hint").textContent = hint;
        return tile;
      }),
    );
  }

  /** Gantt of every visit, one lane per bay, with outages hatched. */
  function renderTimeline(payload, colors) {
    const id = "timeline-chart";
    const bays = payload.meta.bays;
    const element = document.getElementById(id);
    element.style.height = `${bays.length * 34 + 90}px`;
    if (setEmpty(id, payload.meta.coverage_runs.length ? null : "No captures in this range.")) return;
    const lane = Object.fromEntries(bays.map((bay, index) => [bay.id, index]));
    const kinds = Object.fromEntries(bays.map((bay) => [bay.id, bay.kind]));
    const { axis, tooltip, textStyle } = chrome(colors);
    const [zoomStart, zoomEnd] = state.zoom || defaultZoom(payload);
    const instance = chart(id);
    instance.setOption(
      {
        textStyle,
        animation: false,
        grid: { left: 72, right: 16, top: 8, bottom: 56 },
        tooltip: {
          ...tooltip,
          formatter: ({ data }) => {
            const visit = data.visit;
            const bay = bays[lane[visit.bay_id]].label;
            const status = {
              left: `up to ${dollars(visit.revenue, 2)}`,
              open: "still here",
              gap: "cut off by an outage",
            }[visit.end_reason];
            return `<b>${bay}</b><br>${dateTime(visit.start)} – ${timeOfDay(visit.end)}<br>` +
              `${minutesText(visit.duration_s)} · ${status}`;
          },
        },
        xAxis: {
          type: "time",
          min: payload.meta.range.start_ms,
          max: payload.meta.range.end_ms,
          ...axis,
          splitLine: { show: true, lineStyle: { color: colors.grid } },
        },
        yAxis: {
          type: "category",
          data: bays.map((bay) => bay.label),
          inverse: true,
          ...axis,
          splitLine: { show: false },
        },
        dataZoom: [
          { type: "inside", xAxisIndex: 0, startValue: zoomStart, endValue: zoomEnd, filterMode: "weakFilter" },
          {
            type: "slider",
            xAxisIndex: 0,
            startValue: zoomStart,
            endValue: zoomEnd,
            height: 22,
            bottom: 8,
            filterMode: "weakFilter",
            borderColor: colors.border,
            fillerColor: "rgba(57,135,229,0.15)",
            textStyle: { color: colors.muted },
            dataBackground: { lineStyle: { color: colors.axis }, areaStyle: { color: colors.grid } },
          },
        ],
        series: [
          {
            type: "custom",
            // Each visit is a rounded bar spanning its lane's middle.
            renderItem: (params, api) => {
              const start = api.coord([api.value(0), api.value(2)]);
              const end = api.coord([api.value(1), api.value(2)]);
              const height = api.size([0, 1])[1] * 0.6;
              const shape = echarts.graphic.clipRectByRect(
                { x: start[0], y: start[1] - height / 2, width: Math.max(end[0] - start[0], 2), height },
                { x: params.coordSys.x, y: params.coordSys.y, width: params.coordSys.width, height: params.coordSys.height },
              );
              return shape && {
                type: "rect",
                shape: { ...shape, r: 3 },
                style: api.style(),
              };
            },
            encode: { x: [0, 1], y: 2 },
            data: payload.sessions.map((visit) => ({
              value: [visit.start, visit.end, lane[visit.bay_id]],
              visit,
              itemStyle: {
                color: bayColor(colors, kinds[visit.bay_id]),
                // In-progress and cut-off visits read lighter than finished ones.
                opacity: visit.end_reason === "left" ? 1 : 0.5,
              },
            })),
            markArea: outageArea(payload, colors),
          },
        ],
      },
      { notMerge: true },
    );
  }

  /** Step chart of how many wash bays were busy, plus the time split by level. */
  function renderBusy(payload, colors) {
    const id = "busy-chart";
    const { concurrency, summary } = payload;
    const washBays = concurrency.wash_bays;
    const sub = document.getElementById("busy-sub");
    const levels = document.getElementById("busy-levels");
    if (setEmpty(id, summary.covered_s ? null : "No captures in this range.")) {
      sub.textContent = "";
      levels.replaceChildren();
      return;
    }
    const fullMinutes = concurrency.level_seconds[washBays] / 60;
    sub.textContent =
      `All ${washBays} wash bays were taken ${percent(summary.full_share)} of captured time ` +
      `(${Math.round(fullMinutes)} min): the moments a new customer may have driven off.`;
    const { axis, tooltip, textStyle } = chrome(colors);
    const [zoomStart, zoomEnd] = state.zoom || defaultZoom(payload);
    chart(id).setOption(
      {
        textStyle,
        animation: false,
        grid: { left: 40, right: 16, top: 22, bottom: 28 },
        tooltip: {
          ...tooltip,
          trigger: "axis",
          axisPointer: { type: "line", lineStyle: { color: colors.axis } },
          formatter: ([point]) =>
            `${dateTime(point.value[0])}<br><b>${point.value[1]}</b> of ${washBays} wash bays busy`,
        },
        xAxis: {
          type: "time",
          min: payload.meta.range.start_ms,
          max: payload.meta.range.end_ms,
          ...axis,
          splitLine: { show: false },
        },
        yAxis: {
          type: "value",
          min: 0,
          max: washBays,
          interval: 1,
          ...axis,
        },
        // Follows the timeline's zoom through the shared group.
        dataZoom: [{ type: "inside", xAxisIndex: 0, startValue: zoomStart, endValue: zoomEnd, zoomOnMouseWheel: false, moveOnMouseMove: false }],
        series: [
          {
            type: "line",
            step: "end",
            symbol: "none",
            data: concurrency.steps,
            lineStyle: { width: 2, color: colors.wash },
            areaStyle: { color: colors.wash, opacity: 0.18 },
            markLine: {
              silent: true,
              symbol: "none",
              lineStyle: { color: colors.muted, type: "dashed" },
              label: { formatter: "all busy", color: colors.muted, position: "insideEndTop" },
              data: [{ yAxis: washBays }],
            },
            markArea: outageArea(payload, colors),
          },
        ],
      },
      { notMerge: true },
    );

    // A proportion strip: share of captured time at 0, 1, ... all bays busy.
    const total = concurrency.level_seconds.reduce((sum, value) => sum + value, 0) || 1;
    const segments = concurrency.level_seconds.map((seconds, level) => {
      const span = document.createElement("span");
      span.style.flex = String(seconds / total);
      span.style.background = colors.ramp[Math.min(level, colors.ramp.length - 1)];
      span.title = `${level} busy: ${percent(seconds / total)} of captured time`;
      return span;
    });
    const legend = document.createElement("div");
    legend.className = "levels-legend";
    legend.innerHTML = concurrency.level_seconds
      .map(
        (seconds, level) =>
          `<span><i style="background:${colors.ramp[Math.min(level, colors.ramp.length - 1)]}"></i>` +
          `${level} busy ${percent(seconds / total)}</span>`,
      )
      .join("");
    const strip = document.createElement("div");
    strip.className = "levels";
    strip.replaceChildren(...segments);
    levels.replaceChildren(strip, legend);
  }

  /** The focus day's cumulative revenue against a typical earlier day. */
  function renderPace(payload, colors) {
    const id = "pace-chart";
    const pace = payload.pace;
    const sub = document.getElementById("pace-sub");
    const last = pace.focus[pace.focus.length - 1];
    const dayName = new Date(`${pace.day}T12:00`).toLocaleDateString(undefined, {
      weekday: "long",
      month: "short",
      day: "numeric",
    });
    // Only today is still in progress.
    const isToday = pace.day === isoDate(new Date(payload.meta.now));
    if (!pace.typical) {
      sub.textContent =
        `${dayName}: up to ${dollars(last[1])}${isToday ? " so far" : ""}. The typical-day curve appears once ` +
        "there is an earlier day with at least half of it captured.";
    } else {
      // Compare against the typical curve at the same minute of the day.
      const typicalNow = pace.typical.filter(([minute]) => minute <= last[0]).pop()[1];
      const diff = last[1] - typicalNow;
      sub.textContent =
        `${dayName}: up to ${dollars(last[1])} by ${minuteLabel(last[0])}, ` +
        `${dollars(Math.abs(diff))} ${diff >= 0 ? "ahead of" : "behind"} a typical day ` +
        `(average of ${pace.baseline_days.length} earlier day${pace.baseline_days.length === 1 ? "" : "s"}).`;
    }
    if (setEmpty(id, last[1] > 0 || pace.typical ? null : "No complete visits on this day yet.")) return;
    const { axis, tooltip, textStyle } = chrome(colors);
    const series = [
      {
        name: dayName,
        type: "line",
        symbol: "none",
        data: pace.focus,
        lineStyle: { width: 2, color: colors.wash },
        itemStyle: { color: colors.wash },
        endLabel: { show: true, formatter: ({ value }) => dollars(value[1]), color: colors.text },
      },
    ];
    if (pace.typical) {
      series.push({
        name: "Typical day",
        type: "line",
        symbol: "none",
        data: pace.typical,
        lineStyle: { width: 2, color: colors.baseline, type: "dashed" },
        itemStyle: { color: colors.baseline },
      });
    }
    chart(id).setOption(
      {
        textStyle,
        animation: false,
        legend: series.length > 1
          ? { top: 0, right: 0, textStyle: { color: colors.text2, fontSize: 11 }, icon: "roundRect", itemHeight: 3 }
          : undefined,
        grid: { left: 48, right: 56, top: series.length > 1 ? 28 : 12, bottom: 28 },
        tooltip: {
          ...tooltip,
          trigger: "axis",
          axisPointer: { type: "line", lineStyle: { color: colors.axis } },
          formatter: (points) =>
            `${minuteLabel(points[0].value[0])}<br>` +
            points.map((point) => `${point.marker}${point.seriesName}: <b>${dollars(point.value[1])}</b>`).join("<br>"),
        },
        xAxis: {
          type: "value",
          min: 0,
          max: 24 * 60,
          interval: 180,
          ...axis,
          axisLabel: { ...axis.axisLabel, formatter: minuteLabel },
          splitLine: { show: false },
        },
        yAxis: {
          type: "value",
          ...axis,
          axisLabel: { ...axis.axisLabel, formatter: (value) => dollars(value) },
        },
        series,
      },
      { notMerge: true },
    );
  }

  /**
   * Beeswarm offsets: points in the same duration bin stack alternately above
   * and below their lane's center so none hide behind another.
   */
  function dodge(values, binWidth, step, limit) {
    const counts = new Map();
    return values.map((value) => {
      const bin = Math.round(value / binWidth);
      const index = counts.get(bin) || 0;
      counts.set(bin, index + 1);
      const offset = Math.ceil(index / 2) * step * (index % 2 ? 1 : -1);
      return Math.max(-limit, Math.min(limit, offset));
    });
  }

  /** One dot per complete visit, by bay and length, with the minimum-charge zone. */
  function renderSwarm(payload, colors) {
    const id = "swarm-chart";
    const bays = payload.meta.bays;
    const complete = payload.sessions.filter(
      (visit) => visit.end_reason === "left" && visit.start >= payload.meta.range.start_ms,
    );
    const breakEven = payload.meta.pricing.minimum_break_even_s / 60;
    const sub = document.getElementById("swarm-sub");
    sub.textContent =
      `Each dot is a finished visit. Washes left of the ${breakEven.toFixed(0)}-minute line fit in the ` +
      `${payload.meta.pricing.wash_start_tokens}-token start. Longer visits are pinned to the right edge.`;
    const element = document.getElementById(id);
    element.style.height = `${bays.length * 44 + 60}px`;
    if (setEmpty(id, complete.length ? null : "No finished visits in this range.")) return;

    // Cap the axis near the 95th percentile so a few long stays don't squash the rest.
    const minutes = complete.map((visit) => visit.duration_s / 60).sort((a, b) => a - b);
    const p95 = minutes[Math.floor((minutes.length - 1) * 0.95)];
    const max = Math.max(10, Math.ceil((p95 * 1.15) / 5) * 5);
    const { axis, tooltip, textStyle } = chrome(colors);
    const series = bays.map((bay, lane) => {
      const visits = complete.filter((visit) => visit.bay_id === bay.id);
      const values = visits.map((visit) => Math.min(visit.duration_s / 60, max));
      const offsets = dodge(values, max / 90, 0.09, 0.42);
      return {
        name: bay.label,
        type: "scatter",
        symbolSize: 7,
        itemStyle: {
          color: bayColor(colors, bay.kind),
          borderColor: colors.surface,
          borderWidth: 1,
        },
        data: visits.map((visit, index) => ({
          value: [values[index], lane + offsets[index]],
          visit,
          symbol: visit.duration_s / 60 > max ? "triangle" : "circle",
        })),
        markArea:
          lane === 0
            ? {
                silent: true,
                itemStyle: { color: colors.outage },
                label: { show: true, position: "top", color: colors.muted, fontSize: 11, formatter: "minimum charge" },
                data: [[{ xAxis: 0 }, { xAxis: breakEven }]],
              }
            : undefined,
      };
    });
    chart(id).setOption(
      {
        textStyle,
        animation: false,
        grid: { left: 72, right: 20, top: 22, bottom: 36 },
        tooltip: {
          ...tooltip,
          formatter: ({ data, seriesName }) =>
            `<b>${seriesName}</b><br>${dateTime(data.visit.start)}<br>` +
            `${minutesText(data.visit.duration_s)} · up to ${dollars(data.visit.revenue, 2)}`,
        },
        xAxis: {
          type: "value",
          min: 0,
          max,
          name: "minutes",
          nameLocation: "middle",
          nameGap: 24,
          ...axis,
        },
        yAxis: {
          type: "value",
          min: -0.5,
          max: bays.length - 0.5,
          // Half steps put ticks on each lane's center as well as its bounds.
          interval: 0.5,
          inverse: true,
          ...axis,
          splitLine: { show: false },
          axisLabel: {
            ...axis.axisLabel,
            // Only whole lanes get a label; the half-step bounds stay blank.
            formatter: (value) => (Number.isInteger(value) ? bays[value]?.label ?? "" : ""),
          },
        },
        series,
      },
      { notMerge: true },
    );
  }

  /** Mean busy wash bays per hour of day with a p25–p75 band, or a weekday heatmap. */
  function renderHourly(payload, colors) {
    const id = "hourly-chart";
    const washBays = payload.concurrency.wash_bays;
    const days = Math.max(0, ...payload.hourly.map((hour) => hour.days));
    const toggle = document.getElementById("hourly-toggle");
    toggle.hidden = days < WEEKDAY_MIN_DAYS;
    const view = toggle.hidden ? "profile" : state.hourlyView;
    for (const button of toggle.querySelectorAll("button")) {
      button.setAttribute("aria-pressed", String(button.dataset.view === view));
    }
    const sub = document.getElementById("hourly-sub");
    const { axis, tooltip, textStyle } = chrome(colors);
    if (setEmpty(id, days ? null : "Not enough captured time in any hour yet.")) {
      sub.textContent = "";
      return;
    }

    if (view === "weekday") {
      sub.textContent = "Average wash bays busy by weekday and hour, pooled over the range.";
      const names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
      chart(id).setOption(
        {
          textStyle,
          animation: false,
          grid: { left: 40, right: 16, top: 8, bottom: 56 },
          tooltip: {
            ...tooltip,
            formatter: ({ value }) =>
              `${names[value[1]]} ${pad(value[0])}:00<br><b>${value[2].toFixed(1)}</b> bays busy on average`,
          },
          xAxis: { type: "category", data: [...Array(24).keys()].map(pad), ...axis, splitArea: { show: false } },
          yAxis: { type: "category", data: names, inverse: true, ...axis },
          visualMap: {
            min: 0,
            max: washBays,
            calculable: false,
            orient: "horizontal",
            left: "center",
            bottom: 0,
            itemHeight: 120,
            textStyle: { color: colors.muted, fontSize: 11 },
            inRange: { color: colors.ramp.slice(1) },
          },
          series: [
            {
              type: "heatmap",
              data: payload.weekday_hours.map(([weekday, hour, value]) => [hour, weekday, value]),
              itemStyle: { borderColor: colors.surface, borderWidth: 2, borderRadius: 3 },
            },
          ],
        },
        { notMerge: true },
      );
      return;
    }

    const peak = payload.hourly.reduce(
      (best, hour) => (hour.mean != null && (best == null || hour.mean > best.mean) ? hour : best),
      null,
    );
    sub.textContent =
      `Average wash bays busy in each hour of the day` +
      (days > 1 ? `, with the middle half of ${days} days shaded.` : ".") +
      (peak ? ` Busiest: ${pad(peak.hour)}:00 with ${peak.mean.toFixed(1)} of ${washBays} bays.` : "");
    const hours = payload.hourly;
    chart(id).setOption(
      {
        textStyle,
        animation: false,
        grid: { left: 40, right: 16, top: 12, bottom: 28 },
        tooltip: {
          ...tooltip,
          trigger: "axis",
          axisPointer: { type: "line", lineStyle: { color: colors.axis } },
          formatter: (points) => {
            const hour = hours[points[0].dataIndex];
            if (hour.mean == null) return `${pad(hour.hour)}:00<br>not enough captured time`;
            const band = hour.days > 1 ? `<br>middle half ${hour.p25.toFixed(1)}–${hour.p75.toFixed(1)}` : "";
            return `${pad(hour.hour)}:00<br><b>${hour.mean.toFixed(1)}</b> bays busy on average${band}` +
              `<br>${hour.days} day${hour.days === 1 ? "" : "s"}`;
          },
        },
        xAxis: {
          type: "category",
          data: hours.map((hour) => `${pad(hour.hour)}:00`),
          boundaryGap: false,
          ...axis,
          axisLabel: { ...axis.axisLabel, interval: 2 },
        },
        yAxis: { type: "value", min: 0, max: washBays, interval: 1, ...axis },
        series: [
          // The band is an invisible floor at p25 with p75 − p25 stacked on it.
          {
            type: "line",
            stack: "band",
            symbol: "none",
            silent: true,
            lineStyle: { opacity: 0 },
            data: hours.map((hour) => hour.p25),
          },
          {
            type: "line",
            stack: "band",
            symbol: "none",
            silent: true,
            lineStyle: { opacity: 0 },
            areaStyle: { color: colors.wash, opacity: 0.15 },
            data: hours.map((hour) => (hour.p25 == null ? null : hour.p75 - hour.p25)),
          },
          {
            type: "line",
            symbol: "circle",
            symbolSize: 6,
            showSymbol: false,
            data: hours.map((hour) => hour.mean),
            lineStyle: { width: 2, color: colors.wash },
            itemStyle: { color: colors.wash },
          },
        ],
      },
      { notMerge: true },
    );
  }

  /** Each wash bay's share of occupied time and of revenue, against an even split. */
  function renderBalance(payload, colors) {
    const id = "balance-chart";
    const washIds = new Set(payload.meta.bays.filter((bay) => bay.kind === "wash").map((bay) => bay.id));
    const rows = payload.balance.filter((row) => washIds.has(row.bay_id));
    const labels = Object.fromEntries(payload.meta.bays.map((bay) => [bay.id, bay.label]));
    const occupied = rows.reduce((sum, row) => sum + row.occupied_s, 0);
    const revenue = rows.reduce((sum, row) => sum + row.revenue, 0);
    if (setEmpty(id, occupied ? null : "No wash-bay visits in this range.")) return;
    const { axis, tooltip, textStyle } = chrome(colors);
    const even = 1 / rows.length;
    const bar = (name, color, values) => ({
      name,
      type: "bar",
      barMaxWidth: 16,
      barGap: "25%",
      itemStyle: { color, borderRadius: [0, 4, 4, 0] },
      data: values,
    });
    chart(id).setOption(
      {
        textStyle,
        animation: false,
        legend: { top: 0, right: 0, textStyle: { color: colors.text2, fontSize: 11 }, icon: "roundRect", itemWidth: 10, itemHeight: 10 },
        grid: { left: 56, right: 24, top: 40, bottom: 24 },
        tooltip: {
          ...tooltip,
          trigger: "axis",
          axisPointer: { type: "shadow", shadowStyle: { color: colors.outage } },
          formatter: (points) => {
            const row = rows[points[0].dataIndex];
            return `<b>${labels[row.bay_id]}</b><br>` +
              points.map((point) => `${point.marker}${point.seriesName}: <b>${percent(point.value)}</b>`).join("<br>") +
              `<br>busy ${percent(row.share)} of captured time · ${row.visits} visits · up to ${dollars(row.revenue)}`;
          },
        },
        xAxis: {
          type: "value",
          min: 0,
          ...axis,
          axisLabel: { ...axis.axisLabel, formatter: (value) => percent(value) },
        },
        yAxis: { type: "category", data: rows.map((row) => labels[row.bay_id]), inverse: true, ...axis, splitLine: { show: false } },
        series: [
          {
            ...bar("Occupied time", colors.wash, rows.map((row) => row.occupied_s / occupied)),
            markLine: {
              silent: true,
              symbol: "none",
              lineStyle: { color: colors.muted, type: "dashed" },
              // The category axis is inverted, so "start" is the top end.
              label: { formatter: "even", color: colors.muted, position: "start" },
              data: [{ xAxis: even }],
            },
          },
          bar("Revenue", colors.revenue, rows.map((row) => (revenue ? row.revenue / revenue : 0))),
        ],
      },
      { notMerge: true },
    );
  }

  /** GitHub-style calendar of daily estimated revenue. */
  function renderCalendar(payload, colors) {
    const id = "calendar-chart";
    const daily = payload.daily;
    const captured = daily.filter((day) => day.covered_s > 0);
    if (setEmpty(id, captured.length ? null : "No captures in the last 13 weeks.")) return;
    const peak = Math.max(1, ...captured.map((day) => day.revenue));
    const { tooltip, textStyle } = chrome(colors);
    const byDate = Object.fromEntries(daily.map((day) => [day.date, day]));
    chart(id).setOption(
      {
        textStyle,
        animation: false,
        tooltip: {
          ...tooltip,
          formatter: ({ value }) => {
            const day = byDate[value[0]];
            const name = new Date(`${day.date}T12:00`).toLocaleDateString(undefined, {
              weekday: "short",
              month: "short",
              day: "numeric",
            });
            return day.covered_s
              ? `<b>${name}</b><br>up to ${dollars(day.revenue)} · ${day.visits} visits<br>` +
                  `${(day.covered_s / 3600).toFixed(1)} h captured`
              : `<b>${name}</b><br>no captures`;
          },
        },
        visualMap: {
          show: false,
          min: 0,
          max: peak,
          inRange: { color: colors.ramp.slice(1) },
        },
        calendar: {
          range: [daily[0].date, daily[daily.length - 1].date],
          top: 24,
          left: 36,
          right: 8,
          cellSize: ["auto", 18],
          splitLine: { show: false },
          itemStyle: { color: colors.ramp[0], borderColor: colors.surface, borderWidth: 3 },
          dayLabel: { firstDay: 1, nameMap: "en", color: colors.muted, fontSize: 10 },
          monthLabel: { color: colors.muted, fontSize: 11 },
          yearLabel: { show: false },
        },
        series: [
          {
            type: "heatmap",
            coordinateSystem: "calendar",
            // Uncaptured days are left out so they keep the gray base cell.
            data: captured.map((day) => [day.date, day.revenue]),
          },
        ],
      },
      { notMerge: true },
    );
  }

  function renderPricingNote(payload) {
    const pricing = payload.meta.pricing;
    document.getElementById("pricing-note").textContent =
      `Revenue is the most each visit could have paid: all of its bay time, bought in whole tokens ` +
      `(a ${dollars(pricing.token_price, 2)} token buys ${pricing.token_minutes} min, ` +
      `${pricing.wash_start_tokens} tokens to start). Cars can sit without the meter running, so real ` +
      `takings are lower. The vacuum is ${dollars(pricing.vacuum_rate_per_min, 2)}/min (a placeholder price). ` +
      `Times are ${payload.meta.tz}. Updates every ${POLL_MS / 1000} s.`;
  }

  /** Draw everything from the current payload. */
  function renderAll() {
    const payload = state.payload;
    if (!payload) return;
    const colors = palette();
    renderHealth(payload);
    renderLive(payload);
    renderKpis(payload);
    renderTimeline(payload, colors);
    renderBusy(payload, colors);
    renderPace(payload, colors);
    renderSwarm(payload, colors);
    renderHourly(payload, colors);
    renderBalance(payload, colors);
    renderCalendar(payload, colors);
    renderPricingNote(payload);
    echarts.connect(TIME_GROUP);
  }

  // ---------- range controls ----------

  /** Reflect the payload's range in the presets, inputs, and URL. */
  function syncRangeControls(payload) {
    const { start, end } = payload.meta.range;
    const today = isoDate(new Date(payload.meta.now));
    const startInput = document.getElementById("range-start");
    const endInput = document.getElementById("range-end");
    startInput.value = start;
    endInput.value = end;
    startInput.max = endInput.max = today;
    if (payload.meta.first_day) startInput.min = endInput.min = payload.meta.first_day;
    const presets = {
      today: [today, today],
      7: [addDays(today, -6), today],
      30: [addDays(today, -29), today],
      all: [payload.meta.first_day, today],
    };
    for (const button of root.querySelectorAll("[data-preset]")) {
      const [from, to] = presets[button.dataset.preset];
      button.setAttribute("aria-pressed", String(from === start && to === end));
    }
    const url = new URL(location.href);
    url.searchParams.set("from", start);
    url.searchParams.set("to", end);
    history.replaceState(null, "", url);
  }

  function setRange(start, end) {
    state.start = start;
    state.end = end;
    // A new range starts at its own default zoom.
    state.zoom = null;
    refresh();
  }

  // ---------- data loading ----------

  async function refresh() {
    clearTimeout(state.pollTimer);
    const requestId = ++state.requestId;
    const params = new URLSearchParams();
    if (state.start) params.set("from", state.start);
    if (state.end) params.set("to", state.end);
    try {
      const response = await fetch(`/api/analytics?${params}`, { cache: "no-store" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || response.statusText);
      if (requestId !== state.requestId) return;
      state.payload = body;
      state.clockSkewMs = body.meta.now - Date.now();
      state.start = body.meta.range.start;
      state.end = body.meta.range.end;
      syncRangeControls(body);
      renderAll();
    } catch (error) {
      console.debug("analytics refresh failed", error);
      const health = document.getElementById("health");
      health.textContent = `Could not load analytics: ${error.message}. Retrying…`;
      health.dataset.state = "stale";
    }
    schedulePoll();
  }

  /** Poll only while the tab is visible; hidden tabs catch up on return. */
  function schedulePoll() {
    clearTimeout(state.pollTimer);
    if (document.visibilityState === "visible") {
      state.pollTimer = setTimeout(refresh, POLL_MS);
    }
  }

  // ---------- event listeners ----------

  root.querySelectorAll("[data-preset]").forEach((button) =>
    button.addEventListener("click", () => {
      const today = isoDate(new Date(Date.now() + state.clockSkewMs));
      const preset = button.dataset.preset;
      const firstDay = state.payload?.meta.first_day ?? today;
      const start = {
        today,
        7: addDays(today, -6),
        30: addDays(today, -29),
        all: firstDay,
      }[preset];
      setRange(start, today);
    }),
  );

  for (const inputId of ["range-start", "range-end"]) {
    document.getElementById(inputId).addEventListener("change", () => {
      const start = document.getElementById("range-start").value;
      const end = document.getElementById("range-end").value;
      if (start && end && start <= end) setRange(start, end);
    });
  }

  document.getElementById("hourly-toggle").addEventListener("click", (event) => {
    const view = event.target.closest("button")?.dataset.view;
    if (!view) return;
    state.hourlyView = view;
    renderHourly(state.payload, palette());
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refresh();
    else clearTimeout(state.pollTimer);
  });

  // Redraw in the other theme's colors when the OS theme flips.
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", renderAll);

  // ---------- start ----------

  const params = new URLSearchParams(location.search);
  state.start = params.get("from");
  state.end = params.get("to");
  // Both time charts join one group, so zooming either moves both.
  chart("timeline-chart").group = TIME_GROUP;
  chart("busy-chart").group = TIME_GROUP;
  chart("timeline-chart").on("datazoom", () => {
    const zoom = chart("timeline-chart").getOption().dataZoom[0];
    state.zoom = [zoom.startValue, zoom.endValue];
  });
  setInterval(tickLive, 1000);
  refresh();
})();
