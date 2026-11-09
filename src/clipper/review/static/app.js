// The review page (ADR 0024). List the clips, play the source video
// anywhere, trim with a film strip and two handles (3b), Save or Trash each
// clip with its title and notes (3c), re-rendered on Save (3d), and add
// clips no draft proposed (3e).
"use strict";

const player = document.getElementById("player");
const list = document.getElementById("clips");
const strip = document.getElementById("strip");
const tiles = document.getElementById("tiles");
const titleBox = document.getElementById("title-box");
const noteBox = document.getElementById("note-box");
const saveButton = document.getElementById("save");
const trashButton = document.getElementById("trash");

// Seconds per frame in the strip, from zoomed in to zoomed out. Fixed
// steps keep frames on a grid, so views share cached frames.
const STEPS = [1, 2, 5, 10, 15, 30, 60];
const TILE_WIDTH = 96;
// The shortest span the handles allow, in seconds.
const MIN_SPAN = 0.5;
// Lengths outside this are shown in the warning colour (ADR 0023).
const MIN_LENGTH = 15;
const MAX_LENGTH = 90;
// How long Trash waits for its confirming second click, in milliseconds.
const CONFIRM_MS = 3000;
// The strip's first view: a clip plus this much either side (the server's
// RANGE_SECONDS, for clips made in the page).
const RANGE_SECONDS = 120;

let duration = 0;
let current = null;
// Keyed by `clip.key`: the rank, or `new-<n>` for a clip added in the page,
// which keeps its key once Save gives it a rank.
const buttons = new Map(); // key -> the clip's button in the list
const edits = new Map(); // key -> {start, end}, kept while the page is open
let trashArmed = null; // the timer while Trash waits for its second click
let busy = false; // while a Save is waiting on the server (a re-render)
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
  drawUnsaved();
}

// What is on disk for a clip: its label's span, else the draft's; its
// manifest title; its judgment's note.
function savedState(clip) {
  const judged = clip.judgment && clip.judgment.start !== undefined;
  return {
    start: judged ? clip.judgment.start : clip.start,
    end: judged ? clip.judgment.end : clip.end,
    title: clip.title || "",
    note: (clip.judgment && clip.judgment.note) || "",
  };
}

// Compared to the hundredth of a second the handles keep.
function sameTime(a, b) {
  return Math.round(a * 100) === Math.round(b * 100);
}

function drawUnsaved() {
  if (!current) {
    return;
  }
  const saved = savedState(current);
  const changed =
    current.isNew ||
    !sameTime(span.start, saved.start) ||
    !sameTime(span.end, saved.end) ||
    titleBox.value.trim() !== saved.title ||
    noteBox.value.trim() !== saved.note;
  document.getElementById("unsaved").hidden = !changed;
}

function badgeText(clip) {
  if (clip.isNew) {
    return "New";
  }
  if (clip.reviewed === "trashed") {
    return "Trashed";
  }
  if (clip.added) {
    return "Added";
  }
  if (clip.judgment && clip.judgment.verdict === "fix") {
    return "Fix";
  }
  if (clip.judgment && clip.judgment.verdict === "post") {
    return "Post";
  }
  return "";
}

function clipName(clip) {
  if (clip.title) {
    return clip.title;
  }
  if (clip.isNew) {
    return "New clip";
  }
  return clip.error && !clip.file ? "Render failed" : `Clip ${clip.rank}`;
}

function drawBadge(clip) {
  const button = buttons.get(clip.key);
  const badge = button.querySelector(".badge");
  badge.textContent = badgeText(clip);
  badge.dataset.state = badgeText(clip).toLowerCase();
  button.querySelector(".rank").textContent = clip.isNew ? "+" : clip.rank;
  const name = button.querySelector(".name");
  name.textContent = clipName(clip);
  name.classList.toggle("failed", Boolean(clip.error) && !clip.file);
  button.querySelector(".span").textContent = `${fmt(clip.start)} - ${fmt(clip.end)}`;
}

// The parts of the details that follow the manifest: the hook, the
// draft's span (which a re-render keeps), and a render error.
function drawClipInfo(clip) {
  text("hook", clip.hook || "");
  text("clip-span", `${fmt(clip.draft_start)} - ${fmt(clip.draft_end)}`);
  const error = document.getElementById("error");
  const failed = Boolean(clip.error) && !clip.file;
  error.hidden = !failed;
  error.textContent = failed ? `Render failed: ${clip.error}` : "";
}

// --- Choosing a clip ---

function select(clip, button) {
  current = clip;
  for (const other of list.querySelectorAll("button")) {
    other.setAttribute("aria-current", String(other === button));
  }
  if (!edits.has(clip.key)) {
    const saved = savedState(clip);
    edits.set(clip.key, { start: saved.start, end: saved.end });
  }
  span = edits.get(clip.key);
  preview = null;
  disarmTrash();
  notice("");
  judgeNotice(
    clip.reviewed === "trashed" ? "Trashed. Save renders it again." : "",
  );
  document.getElementById("empty").hidden = true;
  document.getElementById("details").hidden = false;
  titleBox.value = clip.title || "";
  noteBox.value = (clip.judgment && clip.judgment.note) || "";
  drawJudged(clip);
  drawClipInfo(clip);
  player.currentTime = span.start;
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

// Back to the draft: its span and the model's title. Notes stay; nothing
// is written until Save.
document.getElementById("reset").addEventListener("click", () => {
  span.start = current.draft_start;
  span.end = current.draft_end;
  titleBox.value = current.model_title || "";
  notice("");
  fit();
});

// --- Save and Trash ---

function judgeNotice(message, failed = false) {
  const line = document.getElementById("judge-notice");
  line.textContent = message;
  line.classList.toggle("error", failed);
}

// A trashed clip can be saved again (it is re-rendered), not trashed again.
// While a Save is rendering, neither button is pressed again.
function drawJudged(clip) {
  saveButton.disabled = busy;
  trashButton.disabled = busy || clip.reviewed === "trashed";
  text("title", clipName(clip));
}

// Whether Save will re-render: the span differs from what the file covers,
// or there is no file.
function rendersOnSave(clip) {
  return !clip.file || !sameTime(span.start, clip.start) || !sameTime(span.end, clip.end);
}

function savedMessage(reply, rendered) {
  if (reply.render_error) {
    const kept = reply.file ? " The old file is kept." : "";
    return [`Label saved; re-render failed: ${reply.render_error}.${kept}`, true];
  }
  if (reply.warning) {
    return [reply.warning, true];
  }
  const how = rendered ? "; re-rendered" : "";
  return [`Saved as ${reply.judgment.verdict}${how}.`, false];
}

async function post(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || `The server answered ${response.status}`);
  }
  return data;
}

// The server's reply is the clip as it now stands.
function judged(clip, reply) {
  Object.assign(clip, reply);
  drawBadge(clip);
  if (clip === current) {
    drawJudged(clip);
    drawClipInfo(clip);
    drawUnsaved();
  }
}

saveButton.addEventListener("click", async () => {
  const clip = current;
  const rendered = rendersOnSave(clip);
  disarmTrash();
  busy = true;
  saveButton.textContent = rendered ? "Rendering…" : "Saving…";
  drawJudged(clip);
  judgeNotice(rendered ? "Re-rendering the clip; this takes a few seconds." : "");
  try {
    const path = clip.isNew ? "/api/clips/add" : `/api/clips/${clip.rank}/save`;
    const reply = await post(path, {
      start: span.start,
      end: span.end,
      title: titleBox.value,
      note: noteBox.value,
    });
    clip.isNew = false;
    judged(clip, reply);
    if (clip === current) {
      judgeNotice(...savedMessage(reply, rendered));
    }
  } catch (error) {
    if (clip === current) {
      judgeNotice(error.message, true);
    }
  } finally {
    busy = false;
    saveButton.textContent = "Save";
    drawJudged(current);
  }
});

function disarmTrash() {
  clearTimeout(trashArmed);
  trashArmed = null;
  trashButton.textContent = "Trash";
  trashButton.classList.remove("armed");
}

trashButton.addEventListener("click", async () => {
  if (trashArmed === null) {
    trashButton.textContent = "Confirm trash";
    trashButton.classList.add("armed");
    trashArmed = setTimeout(disarmTrash, CONFIRM_MS);
    return;
  }
  disarmTrash();
  const clip = current;
  if (clip.isNew) {
    removeClip(clip);
    return;
  }
  try {
    const reply = await post(`/api/clips/${clip.rank}/trash`, {
      note: noteBox.value,
    });
    if (reply.removed !== undefined) {
      removeClip(clip);
      return;
    }
    judged(clip, reply);
    judgeNotice("Trashed; the clip's file is deleted.");
  } catch (error) {
    judgeNotice(error.message, true);
  }
});

// Take a clip out of the list: an unsaved new one, or an added one the
// server removed.
function removeClip(clip) {
  buttons.get(clip.key).closest("li").remove();
  buttons.delete(clip.key);
  edits.delete(clip.key);
  if (clip === current) {
    current = null;
    span = null;
    preview = null;
    document.getElementById("details").hidden = true;
    document.getElementById("empty").hidden = false;
  }
}

// --- Add clip: a moment no draft proposed ---

let newClips = 0;
// How long a new clip starts out, in seconds; then set with the handles.
const NEW_LENGTH = 30;

function addListItem(clip) {
  const item = document.createElement("li");
  const button = document.createElement("button");
  button.type = "button";
  const rank = document.createElement("span");
  rank.className = "rank";
  const name = document.createElement("span");
  name.className = "name";
  const clipSpan = document.createElement("span");
  clipSpan.className = "span";
  const badge = document.createElement("span");
  badge.className = "badge";
  button.append(rank, name, badge, clipSpan);
  button.addEventListener("click", () => select(clip, button));
  item.append(button);
  list.append(item);
  buttons.set(clip.key, button);
  drawBadge(clip);
  return button;
}

document.getElementById("add").addEventListener("click", () => {
  const start = Math.min(Math.max(player.currentTime, 0), Math.max(duration - 1, 0));
  const end = Math.min(start + NEW_LENGTH, duration);
  newClips += 1;
  const clip = {
    key: `new-${newClips}`,
    isNew: true,
    added: true,
    rank: null,
    start,
    end,
    draft_start: start,
    draft_end: end,
    range_start: Math.max(start - RANGE_SECONDS, 0),
    range_end: Math.min(end + RANGE_SECONDS, duration),
    title: null,
    model_title: null,
    hook: null,
    file: null,
    error: null,
    reviewed: null,
    judgment: null,
  };
  select(clip, addListItem(clip));
  judgeNotice("New clip: set the handles, then Save to render it.");
});

titleBox.addEventListener("input", drawUnsaved);
noteBox.addEventListener("input", drawUnsaved);

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
    // The key stays the same when a new clip gets its rank on Save.
    clip.key = clip.rank;
    addListItem(clip);
  }
}

load();
