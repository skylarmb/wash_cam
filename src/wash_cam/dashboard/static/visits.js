// Visit labeling tab: step through one bay's frames and mark when cars
// arrive, wash, and leave. Every change saves immediately.
(() => {
  "use strict";

  // Events in the order they apply within one frame (see visit_labels.EVENTS).
  const EVENTS = ["arrive", "wash_start", "wash_stop", "leave"];
  const EVENT_NAMES = {
    arrive: "Arrive",
    wash_start: "Wash start",
    wash_stop: "Stop washing",
    leave: "Leave",
  };
  const EVENT_KEYS = { a: "arrive", w: "wash_start", s: "wash_stop", l: "leave" };
  // Holding an arrow waits this long, then plays at PLAY_FPS (Shift: FAST_FPS).
  const HOLD_DELAY_MS = 280;
  const PLAY_FPS = 8;
  const FAST_FPS = 24;
  // Frames kept loaded ahead of and behind the current one.
  const PRELOAD_AHEAD = 60;
  const PRELOAD_BEHIND = 15;
  // The crop is drawn at this multiple of its pixel size for a sharp canvas.
  const CANVAS_SCALE = 2;

  const root = document.getElementById("visits");
  const canvas = document.getElementById("frame");
  const lane = document.getElementById("lane");
  const state = {
    detail: null,
    // image_path -> Set of events, the working copy of the window's labels.
    events: new Map(),
    index: 0,
    images: new Map(),
    holdTimer: null,
    playTimer: null,
    heldKey: null,
    // A first Enter with problems arms finishing; a second one finishes.
    finishArmed: false,
    // Saves still in flight, so the page can say whether all work is stored.
    pendingSaves: 0,
    derived: { states: [], problems: [] },
  };

  // ---------- derived state (mirrors visit_labels.derive_visits) ----------

  /** Each frame's state ("empty", "car", "washing") and any problems. */
  function derive(frames, events) {
    const states = new Array(frames.length).fill("empty");
    const problems = [];
    let inCar = false;
    let washing = false;
    frames.forEach((frame, index) => {
      const marked = events.get(frame.image_path) || new Set();
      // A frame's state is the state after its arrive and wash_start and
      // before its leave, so the arrive and leave frames count as car frames.
      let frameState = inCar ? (washing ? "washing" : "car") : "empty";
      for (const event of EVENTS) {
        if (!marked.has(event)) continue;
        if (event === "arrive") {
          if (inCar) problems.push(`${frame.local}: arrive while a car is already in the bay`);
          inCar = true;
          washing = false;
          frameState = "car";
        } else if (event === "wash_start") {
          if (!inCar) problems.push(`${frame.local}: wash start with no car in the bay`);
          else if (washing) problems.push(`${frame.local}: wash start while already washing`);
          else {
            washing = true;
            frameState = "washing";
          }
        } else if (event === "wash_stop") {
          if (!washing) problems.push(`${frame.local}: stop washing while not washing`);
          else {
            washing = false;
            // The stop frame is the last washing frame.
            frameState = "washing";
          }
        } else if (event === "leave") {
          if (!inCar) {
            problems.push(`${frame.local}: leave with no car in the bay`);
            continue;
          }
          if (washing) problems.push(`${frame.local}: leave while still washing`);
          if (frameState === "empty") frameState = "car";
          inCar = false;
          washing = false;
        }
      }
      states[index] = frameState;
    });
    return { states, problems };
  }

  // ---------- helpers ----------

  const pad = (value) => String(value).padStart(2, "0");

  function token(name) {
    return getComputedStyle(root).getPropertyValue(`--${name}`).trim();
  }

  async function api(path, body) {
    const response = await fetch(path, {
      method: body ? "POST" : "GET",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      cache: "no-store",
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || response.statusText);
    return payload;
  }

  // The frame each task was left on, per browser. Storage can be missing or
  // blocked, so every access is guarded and the page works without it.
  const POSITION_KEY = "washcam.visits.position.";

  function rememberPosition() {
    try {
      localStorage.setItem(POSITION_KEY + state.detail.window.id, currentFrame().image_path);
    } catch {
      // No storage: resuming falls back to the last event.
    }
  }

  function recalledPosition(detail) {
    try {
      const name = localStorage.getItem(POSITION_KEY + detail.window.id);
      return detail.frames.findIndex((frame) => frame.image_path === name);
    } catch {
      return -1;
    }
  }

  /** Show whether every change has reached the server. */
  function setSaveState(saveState, message) {
    const element = document.getElementById("save-state");
    element.dataset.state = saveState;
    element.textContent = message;
  }

  /** Run a save, tracking it so the indicator and page unload know about it. */
  async function save(path, body) {
    state.pendingSaves += 1;
    setSaveState("saving", "Saving…");
    try {
      await api(path, body);
      state.pendingSaves -= 1;
      if (!state.pendingSaves) setSaveState("saved", "All changes saved");
    } catch (error) {
      state.pendingSaves -= 1;
      setSaveState("failed", `Not saved: ${error.message}`);
      throw error;
    }
  }

  function editable() {
    return state.detail && state.detail.window.labeled_by === null;
  }

  function currentFrame() {
    return state.detail.frames[state.index];
  }

  // ---------- images ----------

  function cropUrl(frame) {
    const params = new URLSearchParams({ id: state.detail.window.id, image_path: frame.image_path });
    return `/api/visits/crop?${params}`;
  }

  /** Start loading a frame's crop once; returns its image element. */
  function load(index) {
    const frame = state.detail.frames[index];
    if (!frame) return null;
    let image = state.images.get(frame.image_path);
    if (!image) {
      image = new Image();
      image.decoding = "async";
      image.onload = () => {
        if (currentFrame().image_path === frame.image_path) drawFrame();
      };
      image.src = cropUrl(frame);
      state.images.set(frame.image_path, image);
    }
    return image;
  }

  /** Keep a band of frames around the current one loaded; drop far ones. */
  function preload(direction) {
    const ahead = direction < 0 ? PRELOAD_BEHIND : PRELOAD_AHEAD;
    const behind = direction < 0 ? PRELOAD_AHEAD : PRELOAD_BEHIND;
    for (let offset = 1; offset <= Math.max(ahead, behind); offset += 1) {
      if (offset <= ahead) load(state.index + offset);
      if (offset <= behind) load(state.index - offset);
    }
    // Bound memory on long windows by forgetting frames far from here.
    if (state.images.size > 4 * PRELOAD_AHEAD) {
      const keep = new Set();
      for (let index = state.index - behind; index <= state.index + ahead; index += 1) {
        const frame = state.detail.frames[index];
        if (frame) keep.add(frame.image_path);
      }
      for (const name of state.images.keys()) if (!keep.has(name)) state.images.delete(name);
    }
  }

  // ---------- drawing ----------

  function drawFrame() {
    const image = load(state.index);
    const context = canvas.getContext("2d");
    if (image && image.complete && image.naturalWidth) {
      context.imageSmoothingQuality = "high";
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
    }
  }

  function drawLane() {
    const frames = state.detail.frames;
    const width = lane.clientWidth;
    const ratio = window.devicePixelRatio || 1;
    lane.width = Math.round(width * ratio);
    lane.height = Math.round(44 * ratio);
    const context = lane.getContext("2d");
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    const colors = {
      empty: token("state-empty"),
      car: token("state-car"),
      washing: token("state-washing"),
    };
    const step = width / Math.max(frames.length, 1);
    context.fillStyle = colors.empty;
    context.fillRect(0, 0, width, 44);
    // Runs of one state draw as one rectangle, so there are no seams.
    let runStart = 0;
    const states = state.derived.states;
    for (let index = 1; index <= frames.length; index += 1) {
      if (index === frames.length || states[index] !== states[runStart]) {
        if (states[runStart] !== "empty") {
          context.fillStyle = colors[states[runStart]];
          context.fillRect(runStart * step, 8, (index - runStart) * step, 28);
        }
        runStart = index;
      }
    }
    context.fillStyle = token("event");
    frames.forEach((frame, index) => {
      if (state.events.get(frame.image_path)?.size) {
        context.fillRect(index * step + step / 2 - 1, 4, 2, 36);
      }
    });
    // The cursor: a full-height bar in the focus color.
    context.fillStyle = token("focus");
    context.fillRect(Math.min(state.index * step + step / 2 - 1.5, width - 3), 0, 3, 44);
  }

  function renderStatus() {
    const frame = currentFrame();
    const frames = state.detail.frames;
    const current = state.derived.states[state.index];
    const names = { empty: "Empty", car: "Car present", washing: "Washing" };
    const status = document.getElementById("status");
    status.innerHTML = "";
    const strong = document.createElement("strong");
    strong.textContent = names[current];
    status.append(strong, ` · ${frame.local} · frame ${state.index + 1} of ${frames.length}`);

    const marked = state.events.get(frame.image_path) || new Set();
    const chips = document.getElementById("frame-events");
    chips.replaceChildren(
      ...EVENTS.filter((event) => marked.has(event)).map((event) => {
        const chip = document.createElement("span");
        chip.className = "vl-chip";
        chip.textContent = EVENT_NAMES[event];
        return chip;
      }),
    );
    for (const button of root.querySelectorAll("[data-event]")) {
      button.setAttribute("aria-pressed", String(marked.has(button.dataset.event)));
      button.disabled = !editable();
    }
    document.getElementById("clear").disabled = !editable();

    const problems = state.derived.problems;
    const shown = problems.slice(0, 3).join(" · ");
    document.getElementById("problems").textContent = state.finishArmed
      ? `${problems.length} problem${problems.length === 1 ? "" : "s"} left. Press Enter again to finish anyway. ${shown}`
      : problems.length
        ? `${shown}${problems.length > 3 ? ` · and ${problems.length - 3} more` : ""}`
        : "";
  }

  function render() {
    drawFrame();
    renderStatus();
    drawLane();
  }

  // ---------- navigation ----------

  function seek(index, direction = 1) {
    const last = state.detail.frames.length - 1;
    state.index = Math.max(0, Math.min(last, index));
    state.finishArmed = false;
    preload(direction);
    render();
    rememberPosition();
    return state.index > 0 && state.index < last;
  }

  function step(direction) {
    return seek(state.index + direction, direction);
  }

  /** Jump to the nearest frame with events in a direction. */
  function jumpToEvent(direction) {
    const frames = state.detail.frames;
    for (let index = state.index + direction; index >= 0 && index < frames.length; index += direction) {
      if (state.events.get(frames[index].image_path)?.size) {
        seek(index, direction);
        return;
      }
    }
  }

  function startHold(direction, fast) {
    stopHold();
    state.holdTimer = setTimeout(() => {
      state.playTimer = setInterval(() => {
        if (!step(direction)) stopHold();
      }, 1000 / (fast ? FAST_FPS : PLAY_FPS));
    }, HOLD_DELAY_MS);
  }

  function stopHold() {
    clearTimeout(state.holdTimer);
    clearInterval(state.playTimer);
    state.holdTimer = state.playTimer = null;
    state.heldKey = null;
  }

  // ---------- editing ----------

  function refreshDerived() {
    state.derived = derive(state.detail.frames, state.events);
  }

  /** Toggle an event on the current frame, saving it; undo locally on failure. */
  async function toggle(event) {
    if (!editable()) return;
    const frame = currentFrame();
    const marked = state.events.get(frame.image_path) || new Set();
    const present = !marked.has(event);
    if (present) marked.add(event);
    else marked.delete(event);
    state.events.set(frame.image_path, marked);
    refreshDerived();
    state.finishArmed = false;
    render();
    try {
      await save("/api/visits/event", {
        window_id: state.detail.window.id,
        image_path: frame.image_path,
        event,
        present,
      });
    } catch {
      if (present) marked.delete(event);
      else marked.add(event);
      refreshDerived();
      render();
    }
  }

  async function clearFrame() {
    if (!editable()) return;
    const frame = currentFrame();
    const previous = state.events.get(frame.image_path);
    if (!previous?.size) return;
    state.events.delete(frame.image_path);
    refreshDerived();
    render();
    try {
      await save("/api/visits/clear", { window_id: state.detail.window.id, image_path: frame.image_path });
    } catch {
      state.events.set(frame.image_path, previous);
      refreshDerived();
      render();
    }
  }

  /** Finish this bay and open the next task; problems need a second press. */
  async function finish() {
    if (!editable()) {
      if (state.detail.next_id) openWindow(state.detail.next_id);
      return;
    }
    if (state.derived.problems.length && !state.finishArmed) {
      state.finishArmed = true;
      renderStatus();
      return;
    }
    try {
      await save("/api/visits/complete", { window_id: state.detail.window.id, completed: true });
    } catch {
      return;
    }
    const next = state.detail.next_id;
    if (next) openWindow(next);
    else {
      closeLabeler();
      document.getElementById("task").textContent = "All tasks are finished. Open a new range below.";
    }
    loadWindows();
  }

  /** Leave the labeler for the task list; everything is already saved. */
  function pause() {
    stopHold();
    closeLabeler();
    loadWindows();
    document.getElementById("task").textContent = "Paused. Your labels are saved.";
  }

  // ---------- windows ----------

  function describeRange(window) {
    const [startDay, startTime] = window.start.split(" ");
    const [endDay, endTime] = window.end.split(" ");
    const day = new Date(`${startDay}T12:00`).toLocaleDateString(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric",
    });
    const end = endDay === startDay ? endTime.slice(0, 5) : `${endDay} ${endTime.slice(0, 5)}`;
    return `${day} ${startTime.slice(0, 5)}–${end}`;
  }

  async function openWindow(id) {
    stopHold();
    let detail;
    try {
      detail = await api(`/api/visits/window?id=${id}`);
    } catch (error) {
      document.getElementById("picker-error").textContent = error.message;
      return;
    }
    state.detail = detail;
    state.events = new Map(Object.entries(detail.events).map(([name, events]) => [name, new Set(events)]));
    state.images = new Map();
    state.finishArmed = false;
    refreshDerived();
    const [left, top, right, bottom] = detail.crop;
    canvas.width = (right - left) * CANVAS_SCALE;
    canvas.height = (bottom - top) * CANVAS_SCALE;
    canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
    document.getElementById("rubric").textContent = detail.rubric;
    const window = detail.window;
    const who = window.labeled_by ? ` · ${window.labeled_by} (read-only)` : "";
    document.getElementById("task").textContent =
      `${window.bay} · ${describeRange(window)} · task ${detail.task.index} of ${detail.task.count}` +
      `${window.completed ? " · done" : ""}${who}`;
    document.getElementById("labeler").hidden = false;
    const url = new URL(location.href);
    url.searchParams.set("window", id);
    history.replaceState(null, "", url);
    document.getElementById("resume").hidden = true;
    setSaveState("saved", "All changes saved");
    // Resume on the frame this browser left off, else at the last event.
    const remembered = recalledPosition(detail);
    const lastEvent = detail.frames.findLastIndex((frame) => state.events.get(frame.image_path)?.size);
    seek(remembered >= 0 ? remembered : Math.max(lastEvent, 0));
    markCurrentRow();
  }

  function closeLabeler() {
    state.detail = null;
    document.getElementById("labeler").hidden = true;
    const url = new URL(location.href);
    url.searchParams.delete("window");
    history.replaceState(null, "", url);
  }

  /** Offer the first unfinished task you started, when no task is open. */
  function showResumePrompt(windows) {
    const button = document.getElementById("resume");
    const started = windows
      .filter((window) => !window.labeled_by && !window.completed && window.events)
      .sort((a, b) => a.id - b.id)[0];
    button.hidden = Boolean(state.detail) || !started;
    if (!button.hidden) {
      const events = `${started.events} event${started.events === 1 ? "" : "s"}`;
      button.textContent = `Resume ${started.bay} · ${describeRange(started)} · ${events}`;
      button.onclick = () => openWindow(started.id);
    }
  }

  function markCurrentRow() {
    for (const row of document.querySelectorAll("#windows tr")) {
      row.setAttribute("aria-current", String(Number(row.dataset.id) === state.detail?.window.id));
    }
  }

  async function loadWindows() {
    let windows;
    try {
      windows = await api("/api/visits/windows");
    } catch (error) {
      document.getElementById("picker-error").textContent = error.message;
      return;
    }
    showResumePrompt(windows);
    const body = document.getElementById("windows");
    if (!windows.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 6;
      cell.textContent = "No labeling tasks yet. Open a range above.";
      row.append(cell);
      body.replaceChildren(row);
      return;
    }
    body.replaceChildren(
      ...windows.map((window) => {
        const row = document.createElement("tr");
        row.dataset.id = window.id;
        const status = window.completed ? "Done" : window.events ? "In progress" : "Not started";
        for (const text of [
          describeRange(window),
          window.bay,
          window.labeled_by || "You",
          String(window.events),
          status,
        ]) {
          const cell = document.createElement("td");
          cell.textContent = text;
          if (text === "Done") cell.className = "vl-done";
          row.append(cell);
        }
        const actions = document.createElement("td");
        const open = document.createElement("button");
        open.type = "button";
        open.textContent = !window.completed && window.events ? "Resume" : "Open";
        open.addEventListener("click", () => openWindow(window.id));
        actions.append(open);
        if (!window.labeled_by) {
          // Two clicks delete, so a stray click never loses labels.
          const remove = document.createElement("button");
          remove.type = "button";
          remove.textContent = "Delete";
          remove.addEventListener("click", async () => {
            if (remove.dataset.armed !== "true") {
              remove.dataset.armed = "true";
              remove.textContent = `Delete ${window.events} events?`;
              return;
            }
            try {
              await api("/api/visits/delete", { window_id: window.id });
            } catch (error) {
              document.getElementById("picker-error").textContent = error.message;
              return;
            }
            if (state.detail?.window.id === window.id) closeLabeler();
            loadWindows();
          });
          actions.append(" ", remove);
        }
        row.append(actions);
        return row;
      }),
    );
    markCurrentRow();
  }

  // ---------- event listeners ----------

  document.addEventListener("keydown", (event) => {
    if (!state.detail || event.target.closest("input, textarea, select")) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    const key = event.key;
    if (key === "ArrowRight" || key === "ArrowLeft") {
      event.preventDefault();
      // The browser's own key repeat is ignored; holding plays at a set rate.
      if (event.repeat) return;
      const direction = key === "ArrowRight" ? 1 : -1;
      step(direction);
      startHold(direction, event.shiftKey);
      // Set after startHold, which clears any earlier hold and its key.
      state.heldKey = key;
    } else if (EVENT_KEYS[key.toLowerCase()] && !event.repeat) {
      event.preventDefault();
      toggle(EVENT_KEYS[key.toLowerCase()]);
    } else if ((key === "Backspace" || key === "Delete") && !event.repeat) {
      event.preventDefault();
      clearFrame();
    } else if (key === "[" || key === "]") {
      event.preventDefault();
      jumpToEvent(key === "]" ? 1 : -1);
    } else if (key === "Escape" && !event.repeat) {
      event.preventDefault();
      pause();
    } else if (key === "Enter" && !event.repeat && !event.target.closest("button")) {
      event.preventDefault();
      finish();
    }
  });

  document.addEventListener("keyup", (event) => {
    // Releasing either arrow stops playback, even if the held key changed.
    if (event.key === state.heldKey || event.key === "ArrowLeft" || event.key === "ArrowRight") {
      stopHold();
    }
  });
  // Losing focus mid-hold would otherwise keep playing.
  window.addEventListener("blur", stopHold);

  for (const button of root.querySelectorAll("[data-event]")) {
    button.addEventListener("click", () => toggle(button.dataset.event));
  }
  document.getElementById("clear").addEventListener("click", clearFrame);
  document.getElementById("prev").addEventListener("click", () => step(-1));
  document.getElementById("next").addEventListener("click", () => step(1));
  document.getElementById("prev-event").addEventListener("click", () => jumpToEvent(-1));
  document.getElementById("next-event").addEventListener("click", () => jumpToEvent(1));
  document.getElementById("finish").addEventListener("click", finish);
  document.getElementById("pause").addEventListener("click", pause);
  // Only warn on leaving while a save is still in flight; otherwise all is stored.
  window.addEventListener("beforeunload", (event) => {
    if (state.pendingSaves) event.preventDefault();
  });

  // Click or drag the lane to seek.
  function seekFromLane(event) {
    const box = lane.getBoundingClientRect();
    const share = (event.clientX - box.left) / box.width;
    seek(Math.floor(share * state.detail.frames.length));
  }
  lane.addEventListener("pointerdown", (event) => {
    if (!state.detail) return;
    lane.setPointerCapture(event.pointerId);
    seekFromLane(event);
  });
  lane.addEventListener("pointermove", (event) => {
    if (state.detail && lane.hasPointerCapture(event.pointerId)) seekFromLane(event);
  });
  new ResizeObserver(() => state.detail && drawLane()).observe(lane);
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => state.detail && drawLane());

  document.getElementById("new-range").addEventListener("submit", async (event) => {
    event.preventDefault();
    const error = document.getElementById("picker-error");
    error.textContent = "";
    try {
      const { ids } = await api("/api/visits/windows", {
        start: document.getElementById("range-start").value,
        end: document.getElementById("range-end").value,
      });
      await loadWindows();
      openWindow(ids[0]);
    } catch (problem) {
      error.textContent = problem.message;
    }
  });

  // ---------- start ----------

  // Suggest the last two whole hours as a new range.
  const end = new Date();
  end.setMinutes(0, 0, 0);
  const start = new Date(end.getTime() - 2 * 3600 * 1000);
  const local = (moment) =>
    `${moment.getFullYear()}-${pad(moment.getMonth() + 1)}-${pad(moment.getDate())}T${pad(moment.getHours())}:00`;
  document.getElementById("range-start").value = local(start);
  document.getElementById("range-end").value = local(end);

  loadWindows();
  const requested = Number(new URLSearchParams(location.search).get("window"));
  if (requested) openWindow(requested);
})();
