// The review page (ADR 0024). Items 3a and 3b: list the clips, play the
// source video anywhere, and trim with a film strip and two handles. Edits
// live in the page until Save arrives (3c).
"use strict";

const player = document.getElementById("player");
const list = document.getElementById("clips");
const strip = document.getElementById("strip");
const tiles = document.getElementById("tiles");

// Seconds per frame in the strip, from zoomed in to zoomed out. Fixed
// steps keep frames on a grid, so views share cached frames.
const STEPS = [1, 2, 5, 10, 15, 30, 60];
const TILE_WIDTH = 96;
// The shortest span the handles allow, in seconds.
const MIN_SPAN = 0.5;
// Lengths outside this are shown in the warning colour (ADR 0023).
const MIN_LENGTH = 15;
const MAX_LENGTH = 90;

let duration = 0;
let current = null;
const edits = new Map(); // rank -> {start, end}, kept while the page is open
let span = null; // the chosen clip's edited {start, end}
const view = { start: 0, step: 5, count: 10 };
let preview = null; // {end, settling} while Preview span plays

function fmt(seconds) {
  const whole = Math.floor(seconds);
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const s = whole % 60;
  return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function fmtTenths(seconds) {
  const tenths = Math.floor((seconds % 1) * 10);
  return `${fmt(seconds)}.${tenths}`;
}

function text(id, value) {
  document.getElementById(id).textContent = value;
}

function clamp(value, low, high) {
  return Math.min(Math.max(value, low), high);
}

function round(seconds) {
  return Math.round(seconds * 100) / 100;
}

// --- The view: which part of the video the strip shows ---

function viewEnd() {
  return view.start + view.step * view.count;
}

function setViewStart(start) {
  const widest = Math.max(duration - view.step * view.count, 0);
  const snapped = Math.floor(clamp(start, 0, widest) / view.step) * view.step;
  view.start = Math.max(snapped, 0);
}

function centreOn(seconds) {
  setViewStart(seconds - (view.step * view.count) / 2);
}

function tileCount() {
  return Math.max(Math.floor(strip.clientWidth / TILE_WIDTH), 4);
}

function fit() {
  view.count = tileCount();
  const width = current.range_end - current.range_start;
  view.step = STEPS.find((step) => step * view.count >= width) ?? STEPS.at(-1);
  centreOn((current.range_start + current.range_end) / 2);
  drawStrip();
}

function zoom(direction) {
  const index = clamp(STEPS.indexOf(view.step) + direction, 0, STEPS.length - 1);
  const centre = (view.start + viewEnd()) / 2;
  view.step = STEPS[index];
  centreOn(centre);
  drawStrip();
}

function pan(direction) {
  setViewStart(view.start + direction * view.step * Math.floor(view.count / 2));
  drawStrip();
}

function ensureVisible(seconds) {
  if (seconds < view.start || seconds > viewEnd()) {
    centreOn(seconds);
    drawStrip();
  }
}

// --- Drawing ---

function percent(seconds) {
  return ((seconds - view.start) / (viewEnd() - view.start)) * 100;
}

function place(id, seconds) {
  const element = document.getElementById(id);
  const at = percent(seconds);
  element.hidden = at < 0 || at > 100;
  element.style.left = `${at}%`;
}

function drawStrip() {
  view.count = tileCount();
  tiles.replaceChildren();
  for (let i = 0; i < view.count; i += 1) {
    const second = view.start + i * view.step;
    const tile = document.createElement("div");
    tile.className = "tile";
    if (second < duration) {
      const image = document.createElement("img");
      image.src = `/frames/${second}.jpg`;
      image.alt = "";
      image.addEventListener("error", () => image.remove());
      const label = document.createElement("span");
      label.textContent = fmt(second);
      tile.append(image, label);
    }
    tiles.append(tile);
  }
  text("scale", `${view.step} s per frame`);
  drawMarks();
}

function drawMarks() {
  if (!span) {
    return;
  }
  const left = clamp(percent(span.start), 0, 100);
  const right = clamp(percent(span.end), 0, 100);
  document.getElementById("dim-before").style.width = `${left}%`;
  document.getElementById("dim-after").style.width = `${100 - right}%`;
  const selection = document.getElementById("selection");
  selection.style.left = `${left}%`;
  selection.style.width = `${right - left}%`;
  place("handle-start", span.start);
  place("handle-end", span.end);
  place("playhead", player.currentTime);
  drawTimes();
}

function drawTimes() {
  const length = span.end - span.start;
  text("edit-start", fmtTenths(span.start));
  text("edit-end", fmtTenths(span.end));
  const lengthCell = document.getElementById("edit-length");
  lengthCell.textContent = `${length.toFixed(1)} s`;
  lengthCell.classList.toggle("warn", length < MIN_LENGTH || length > MAX_LENGTH);
  const changed = span.start !== current.start || span.end !== current.end;
  document.getElementById("edited").hidden = !changed;
}

// --- Choosing a clip ---

function select(clip, button) {
  current = clip;
  for (const other of list.querySelectorAll("button")) {
    other.setAttribute("aria-current", String(other === button));
  }
  if (!edits.has(clip.rank)) {
    edits.set(clip.rank, { start: clip.start, end: clip.end });
  }
  span = edits.get(clip.rank);
  preview = null;
  document.getElementById("empty").hidden = true;
  document.getElementById("details").hidden = false;
  text("title", clip.title || `Clip ${clip.rank}`);
  text("hook", clip.hook || "");
  text("clip-span", `${fmt(clip.start)} - ${fmt(clip.end)}`);
  const error = document.getElementById("error");
  error.hidden = !clip.error;
  error.textContent = clip.error ? `Render failed: ${clip.error}` : "";
  player.currentTime = clip.start;
  fit();
}

// --- Moving the playhead ---

for (const button of document.querySelectorAll("[data-step]")) {
  button.addEventListener("click", () => {
    const end = Number.isFinite(player.duration) ? player.duration : Infinity;
    const next = player.currentTime + Number(button.dataset.step);
    player.currentTime = Math.min(Math.max(next, 0), end);
  });
}

function timeAt(clientX) {
  const box = strip.getBoundingClientRect();
  const fraction = (clientX - box.left) / box.width;
  return view.start + fraction * (viewEnd() - view.start);
}

tiles.addEventListener("click", (event) => {
  if (current) {
    player.currentTime = clamp(timeAt(event.clientX), 0, duration);
  }
});

// --- The handles ---

function setStart(seconds) {
  span.start = round(clamp(seconds, 0, span.end - MIN_SPAN));
}

function setEnd(seconds) {
  span.end = round(clamp(seconds, span.start + MIN_SPAN, duration));
}

function dragHandle(handle, move) {
  handle.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    const onMove = (moveEvent) => {
      // Past either edge, the strip pans with the handle.
      const box = strip.getBoundingClientRect();
      if (moveEvent.clientX < box.left && view.start > 0) {
        setViewStart(view.start - view.step);
        drawStrip();
      } else if (moveEvent.clientX > box.right && viewEnd() < duration) {
        setViewStart(view.start + view.step);
        drawStrip();
      }
      const seconds = move(timeAt(moveEvent.clientX));
      player.currentTime = seconds;
      drawMarks();
    };
    const onUp = () => {
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
      handle.removeEventListener("pointercancel", onUp);
    };
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
    handle.addEventListener("pointercancel", onUp);
  });
}

dragHandle(document.getElementById("handle-start"), (seconds) => {
  setStart(seconds);
  return span.start;
});
dragHandle(document.getElementById("handle-end"), (seconds) => {
  setEnd(seconds);
  return span.end;
});

function notice(message) {
  text("trim-notice", message);
}

document.getElementById("set-start").addEventListener("click", () => {
  const seconds = player.currentTime;
  if (seconds > span.end - MIN_SPAN) {
    notice("The start must be before the end.");
    return;
  }
  notice("");
  setStart(seconds);
  ensureVisible(seconds);
  drawMarks();
});

document.getElementById("set-end").addEventListener("click", () => {
  const seconds = player.currentTime;
  if (seconds < span.start + MIN_SPAN) {
    notice("The end must be after the start.");
    return;
  }
  notice("");
  setEnd(seconds);
  ensureVisible(seconds);
  drawMarks();
});

document.getElementById("reset").addEventListener("click", () => {
  span.start = current.start;
  span.end = current.end;
  notice("");
  fit();
});

// --- Preview span: play from the start handle, pause at the end handle ---

document.getElementById("preview").addEventListener("click", () => {
  // A seek fires one `seeking` event of its own; any later one is the
  // user's and cancels the preview.
  const seeks = Math.abs(player.currentTime - span.start) > 0.01;
  preview = { end: span.end, settling: seeks };
  player.currentTime = span.start;
  player.play().catch(() => {
    preview = null;
  });
  requestAnimationFrame(watchPreview);
});

// Checked every frame rather than on `timeupdate`, which fires only a few
// times a second and would play past the end handle.
function watchPreview() {
  if (!preview) {
    return;
  }
  if (!preview.settling && player.currentTime >= preview.end) {
    player.pause();
    return;
  }
  requestAnimationFrame(watchPreview);
}

player.addEventListener("seeking", () => {
  if (preview && preview.settling) {
    preview.settling = false;
  } else {
    preview = null;
  }
});

player.addEventListener("pause", () => {
  preview = null;
});

player.addEventListener("timeupdate", () => {
  if (span) {
    place("playhead", player.currentTime);
  }
});

document.getElementById("zoom-in").addEventListener("click", () => zoom(-1));
document.getElementById("zoom-out").addEventListener("click", () => zoom(1));
document.getElementById("pan-back").addEventListener("click", () => pan(-1));
document.getElementById("pan-on").addEventListener("click", () => pan(1));
document.getElementById("fit").addEventListener("click", () => fit());

window.addEventListener("resize", () => {
  if (current) {
    drawStrip();
  }
});

async function load() {
  const response = await fetch("/api/clips");
  const data = await response.json();
  duration = data.duration;
  text("source", data.source);
  const flags = document.getElementById("flags");
  for (const name of ["vertical", "captions", "titles"]) {
    if (data[name]) {
      const flag = document.createElement("span");
      flag.textContent = name;
      flags.append(flag);
    }
  }
  for (const clip of data.clips) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    const rank = document.createElement("span");
    rank.className = "rank";
    rank.textContent = clip.rank;
    const name = document.createElement("span");
    name.className = clip.error ? "name failed" : "name";
    name.textContent = clip.title || (clip.error ? "Render failed" : `Clip ${clip.rank}`);
    const clipSpan = document.createElement("span");
    clipSpan.className = "span";
    clipSpan.textContent = `${fmt(clip.start)} - ${fmt(clip.end)}`;
    button.append(rank, name, clipSpan);
    button.addEventListener("click", () => select(clip, button));
    item.append(button);
    list.append(item);
  }
}

load();
