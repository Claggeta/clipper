// B-roll overlays (ADR 0034, B-roll amendment; V7 item 9m-2b): a file
// over a stretch of the finished clip, as a cutaway over the whole frame
// or a picture-in-picture box, silent, the clip's sound going on. A lane
// of bars under the Sequence lane in clip time (moved, trimmed, selected),
// a row for the selected one's mode and size, and (in preview.js) the
// overlays drawn on the preview, a box dragged to its place. Kept per clip
// until Save, which re-renders. Loaded after sequence.js, pad.js and
// audio.js, before preview.js.
"use strict";

const overlayLane = document.getElementById("broll-lane");
const overlayRow = document.getElementById("broll-row");
const overlayNote = document.getElementById("broll-note");
// A hidden player for the overlay video on show, muted: overlays are
// silent in the render too. Stills are images, one per file.
const overlayPlayer = document.getElementById("overlay-player");
const overlayStills = new Map();
// key -> [{file, start, end, offset, mode, x, y, size}], clip seconds.
const overlayEdits = new Map();
const MAX_OVERLAYS = 10;
const NEW_OVERLAY_SECONDS = 2;
const MIN_OVERLAY = 0.5;
// The model's defaults (domain/models.py BrollOverlay).
const PIP_X = 0.5;
const PIP_Y = 0.3;
const PIP_SIZE = 0.45;
// Drift past this many seconds puts the overlay player back in step.
const OVERLAY_DRIFT = 0.25;
let selectedOverlay = -1;
// While a bar is dragged, the lane is not rebuilt under the pointer.
let overlayDragging = false;
// Where each picture-in-picture box was last drawn, in the canvas's CSS
// pixels, for dragging: [{index, left, top, width, height}].
let overlayBoxes = [];

function savedOverlays(clip) {
  const judged = clip.judgment && clip.judgment.start !== undefined;
  return (judged ? clip.judgment.broll : clip.broll) || [];
}

function overlaysOf(clip) {
  if (!overlayEdits.has(clip.key)) {
    overlayEdits.set(
      clip.key,
      savedOverlays(clip).map((item) => ({ ...item })),
    );
  }
  return overlayEdits.get(clip.key);
}

function sentOverlays(clip) {
  const fraction = (value) => Math.round(value * 1000) / 1000;
  return overlaysOf(clip).map((item) => ({
    file: item.file,
    start: round(item.start),
    end: round(item.end),
    offset: round(item.offset || 0),
    mode: item.mode,
    x: fraction(item.x),
    y: fraction(item.y),
    size: fraction(item.size),
  }));
}

function sameOverlays(a, b) {
  const text = (items) =>
    JSON.stringify(
      items.map((o) => [
        o.file,
        round(o.start),
        round(o.end),
        round(o.offset || 0),
        o.mode,
        Math.round(o.x * 1000),
        Math.round(o.y * 1000),
        Math.round(o.size * 1000),
      ]),
    );
  return text(a) === text(b);
}

function overlaysChanged() {
  drawUnsaved();
  drawOverlayLane();
}

// How far into its file an overlay may reach: a video's length; a still
// has no end of its own.
function overlayReach(item) {
  if (isStill(item.file)) {
    return Infinity;
  }
  const file = brollFile(item.file);
  return file && file.duration ? file.duration : Infinity;
}

// --- Adding ---

// A new overlay of `file` at clip second `at`: a 2 s cutaway, shorter if
// the clip or the file ends sooner.
function addOverlay(file, at = null) {
  if (!current || !span) {
    return;
  }
  const list = overlaysOf(current);
  if (list.length >= MAX_OVERLAYS) {
    overlayNote.textContent = `At most ${MAX_OVERLAYS} overlays.`;
    return;
  }
  const total = clipLength();
  const here = at === null ? clipSeconds(player.currentTime) : at;
  const start = round(clamp(Number.isFinite(here) ? here : 0, 0, Math.max(total - MIN_OVERLAY, 0)));
  const length = Math.min(NEW_OVERLAY_SECONDS, total - start, file.still ? Infinity : file.duration || Infinity);
  if (length < MIN_OVERLAY) {
    overlayNote.textContent = "Too near the clip's end; move the playhead first.";
    return;
  }
  list.push({
    file: file.name,
    start,
    end: round(start + length),
    offset: 0,
    mode: "cutaway",
    x: PIP_X,
    y: PIP_Y,
    size: PIP_SIZE,
  });
  list.sort((a, b) => a.start - b.start);
  selectedOverlay = list.findIndex((item) => item.start === start && item.file === file.name);
  overlayNote.textContent = "";
  overlaysChanged();
}

// --- The lane: a bar per overlay in clip time ---

// Seconds into the finished clip at a point on the lane.
function overlayTimeAt(clientX) {
  const box = overlayLane.getBoundingClientRect();
  return clamp((clientX - box.left) / box.width, 0, 1) * clipLength();
}

// Show the finished clip at clip second `at`: the block there, at its time.
function seekClip(at) {
  const blocks = blocksOf(current);
  const starts = blockStarts(blocks);
  let index = starts.findIndex((start, i) => at < start + blockLength(blocks[i]));
  if (index < 0) {
    index = blocks.length - 1;
  }
  const into = clamp(at - starts[index], 0, Math.max(blockLength(blocks[index]) - 0.01, 0));
  selectBlock(index, blocks[index].start + into);
}

function drawOverlayLane() {
  if (overlayDragging) {
    return;
  }
  overlayLane.replaceChildren();
  if (!current || !span) {
    overlayRow.hidden = true;
    return;
  }
  const list = overlaysOf(current);
  const total = clipLength();
  selectedOverlay = selectedOverlay < list.length ? selectedOverlay : -1;
  overlayLane.classList.toggle("empty", list.length === 0);
  list.forEach((item, index) => {
    const bar = document.createElement("div");
    bar.className = `overlay-bar ${item.mode}`;
    bar.classList.toggle("selected", index === selectedOverlay);
    bar.classList.toggle("past", item.end > total + 0.05);
    placeBar(bar, item, total);
    const label = document.createElement("span");
    label.textContent = `${item.mode === "pip" ? "◳" : "▣"} ${item.file}`;
    bar.append(overlayGrip(item, "start"), label, overlayGrip(item, "end"));
    moveOverlay(bar, item, index);
    overlayLane.append(bar);
  });
  drawOverlayRow();
}

function placeBar(bar, item, total) {
  const left = total ? clamp(item.start / total, 0, 1) : 0;
  const right = total ? clamp(item.end / total, 0, 1) : 0;
  bar.style.left = `${left * 100}%`;
  bar.style.width = `${Math.max(right - left, 0) * 100}%`;
  const mode = item.mode === "pip" ? "picture-in-picture" : "cutaway";
  bar.title =
    `${item.file}, ${mode}: ${item.start.toFixed(1)} - ${item.end.toFixed(1)} s` +
    ` of the clip, from ${(item.offset || 0).toFixed(1)} s into the file`;
}

// A click selects the overlay and shows the clip there; a drag moves it,
// inside the clip.
function moveOverlay(bar, item, index) {
  bar.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.classList.contains("grip")) {
      return;
    }
    event.preventDefault();
    const fromX = event.clientX;
    const from = item.start;
    const length = item.end - item.start;
    const perPixel = clipLength() / overlayLane.getBoundingClientRect().width;
    let dragging = false;
    bar.setPointerCapture(event.pointerId);
    const onMove = (moveEvent) => {
      if (!dragging && Math.abs(moveEvent.clientX - fromX) < BLOCK_DRAG_PX) {
        return;
      }
      dragging = true;
      overlayDragging = true;
      const start = clamp(from + (moveEvent.clientX - fromX) * perPixel, 0, Math.max(clipLength() - length, 0));
      item.start = round(start);
      item.end = round(start + length);
      placeBar(bar, item, clipLength());
      drawUnsaved();
    };
    const onUp = (upEvent) => {
      bar.removeEventListener("pointermove", onMove);
      bar.removeEventListener("pointerup", onUp);
      bar.removeEventListener("pointercancel", onUp);
      overlayDragging = false;
      selectedOverlay = index;
      if (dragging) {
        const list = overlaysOf(current);
        list.sort((a, b) => a.start - b.start);
        selectedOverlay = list.indexOf(item);
        overlaysChanged();
        return;
      }
      if (upEvent.type === "pointerup") {
        seekClip(overlayTimeAt(upEvent.clientX));
      }
      drawOverlayLane();
    };
    bar.addEventListener("pointermove", onMove);
    bar.addEventListener("pointerup", onUp);
    bar.addEventListener("pointercancel", onUp);
  });
}

// A drag on an overlay's edge trims it. The start takes the file's point
// with it, so the footage stays where it was in the clip.
function overlayGrip(item, edge) {
  const handle = document.createElement("i");
  handle.className = `grip ${edge}`;
  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    handle.setPointerCapture(event.pointerId);
    const bar = handle.parentElement;
    const onMove = (moveEvent) => {
      overlayDragging = true;
      const at = overlayTimeAt(moveEvent.clientX);
      if (edge === "start") {
        // Not before the file's start: the offset stays at 0 or more.
        const floor = Math.max(0, item.start - (item.offset || 0));
        const start = round(clamp(at, floor, item.end - MIN_OVERLAY));
        item.offset = round((item.offset || 0) + start - item.start);
        item.start = start;
      } else {
        const ceiling = Math.min(clipLength(), item.start + overlayReach(item) - (item.offset || 0));
        item.end = round(clamp(at, item.start + MIN_OVERLAY, ceiling));
      }
      placeBar(bar, item, clipLength());
      drawUnsaved();
    };
    const onUp = () => {
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
      handle.removeEventListener("pointercancel", onUp);
      overlayDragging = false;
      overlaysChanged();
    };
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
    handle.addEventListener("pointercancel", onUp);
  });
  return handle;
}

// The selected overlay's mode, size and Remove.
function drawOverlayRow() {
  const list = current ? overlaysOf(current) : [];
  const item = list[selectedOverlay];
  overlayRow.hidden = !item;
  overlayRow.replaceChildren();
  if (!item) {
    return;
  }
  const name = document.createElement("span");
  name.className = "muted";
  name.textContent = item.file;
  const mode = selectInput(
    [
      ["cutaway", "Cutaway"],
      ["pip", "Picture-in-picture"],
    ],
    item.mode,
    (value) => {
      item.mode = value;
      overlaysChanged();
    },
  );
  mode.title = "Over the whole frame, or a box placed by dragging on the preview";
  const size = document.createElement("label");
  size.className = "fade";
  const slider = document.createElement("input");
  slider.type = "range";
  slider.min = "15";
  slider.max = "100";
  slider.step = "1";
  slider.value = String(Math.round(item.size * 100));
  slider.disabled = item.mode !== "pip";
  const shown = document.createElement("span");
  shown.textContent = `${slider.value}%`;
  slider.addEventListener("input", () => {
    item.size = Number(slider.value) / 100;
    shown.textContent = `${slider.value}%`;
    drawUnsaved();
  });
  size.title = "The box's width, of the frame's";
  size.append("Size ", slider, shown);
  const remove = document.createElement("button");
  remove.type = "button";
  remove.textContent = "Remove";
  remove.addEventListener("click", () => {
    list.splice(selectedOverlay, 1);
    selectedOverlay = -1;
    overlaysChanged();
  });
  overlayRow.append(name, mode, size, remove);
}

overlayLane.addEventListener("dragover", (event) => {
  if (event.dataTransfer.types.includes(BROLL_TYPE)) {
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    overlayLane.classList.add("dropping");
  }
});

overlayLane.addEventListener("dragleave", () => {
  overlayLane.classList.remove("dropping");
});

overlayLane.addEventListener("drop", (event) => {
  overlayLane.classList.remove("dropping");
  const name = event.dataTransfer.getData(BROLL_TYPE);
  const file = name ? brollFile(name) : null;
  if (file) {
    event.preventDefault();
    addOverlay(file, overlayTimeAt(event.clientX));
  }
});

// --- What the preview draws ---

// The overlays shown at clip second `at`, in order (later ones on top).
function activeOverlays(at) {
  if (!current || !Number.isFinite(at)) {
    return [];
  }
  return overlaysOf(current)
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => at >= item.start && at < item.end);
}

// The picture of an overlay, once it has one: its still, or the overlay
// player for the first video overlay shown (one player; a second
// overlapping video is not previewed). Null otherwise.
function overlayFrame(item, isFirstVideo) {
  const src = `/broll/${encodeURIComponent(item.file)}`;
  if (isStill(item.file)) {
    let image = overlayStills.get(item.file);
    if (!image) {
      image = new Image();
      image.src = src;
      overlayStills.set(item.file, image);
    }
    return image.complete && image.naturalWidth
      ? { media: image, width: image.naturalWidth, height: image.naturalHeight }
      : null;
  }
  if (!isFirstVideo) {
    return null;
  }
  return overlayPlayer.videoWidth && overlayPlayer.getAttribute("src") === src
    ? { media: overlayPlayer, width: overlayPlayer.videoWidth, height: overlayPlayer.videoHeight }
    : null;
}

// Keep the overlay player on the first video overlay shown, at its time,
// playing while the clip plays.
function syncOverlayPlayer(shown, at, playing) {
  const video = shown.find(({ item }) => !isStill(item.file));
  if (!video) {
    if (!overlayPlayer.paused) {
      overlayPlayer.pause();
    }
    return;
  }
  const { item } = video;
  const src = `/broll/${encodeURIComponent(item.file)}`;
  if (overlayPlayer.getAttribute("src") !== src) {
    overlayPlayer.src = src;
  }
  const wanted = (item.offset || 0) + at - item.start;
  if (overlayPlayer.readyState < 1) {
    return;
  }
  const drift = Math.abs(overlayPlayer.currentTime - wanted);
  if (playing) {
    if (drift > OVERLAY_DRIFT) {
      overlayPlayer.currentTime = wanted;
    }
    if (overlayPlayer.paused) {
      overlayPlayer.play().catch(() => {});
    }
  } else {
    if (!overlayPlayer.paused) {
      overlayPlayer.pause();
    }
    if (drift > 0.04) {
      overlayPlayer.currentTime = wanted;
    }
  }
}

// Draw the overlays at clip second `at`, as the renderer lays them, after
// the framing and zooms and under the captions and texts.
function drawOverlays(context, width, height, at, playing) {
  overlayBoxes = [];
  const shown = activeOverlays(at);
  syncOverlayPlayer(shown, at, playing);
  let firstVideo = true;
  for (const { item, index } of shown) {
    const video = !isStill(item.file);
    const frame = overlayFrame(item, video && firstVideo);
    firstVideo = firstVideo && !video;
    if (!frame) {
      continue;
    }
    if (item.mode === "cutaway") {
      drawFit(context, width, height, frame.media, [frame.width, frame.height]);
      continue;
    }
    const w = item.size * width;
    const h = (w * frame.height) / frame.width;
    const left = item.x * width - w / 2;
    const top = item.y * height - h / 2;
    context.drawImage(frame.media, left, top, w, h);
    if (index === selectedOverlay) {
      context.strokeStyle = "rgb(255 255 255 / 0.6)";
      context.lineWidth = 1;
      context.strokeRect(left + 0.5, top + 0.5, w - 1, h - 1);
    }
    overlayBoxes.push({ index, left, top, width: w, height: h });
  }
}

// Called by app.js when a clip is chosen, saved or reset.
function resetOverlays(clip, clear = false) {
  if (clear) {
    overlayEdits.set(clip.key, []);
  } else {
    overlayEdits.delete(clip.key);
  }
  selectedOverlay = -1;
  overlayPlayer.pause();
  if (clip === current) {
    drawOverlayLane();
  }
}
