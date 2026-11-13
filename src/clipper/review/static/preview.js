// The live preview (ADR 0031, V7 item 9e): the chosen clip as it will
// look, drawn on a canvas from the source player, which plays behind it.
// The span plays through its cuts; framed as fit (the picture across the
// width over a blurred copy) or face (the crop a re-render would use);
// with the word captions and on-screen texts drawn at their burn-in
// sizes. Close, not exact: Save's re-render is the result. Loaded after
// app.js, keys.js and precision.js.
"use strict";

const previewCanvas = document.getElementById("preview-canvas");
const previewTime = document.getElementById("preview-time");
const previewNote = document.getElementById("preview-note");
// The active word's colour in the burn-in (captions.py's _HIGHLIGHT).
const HIGHLIGHT = "#ffff00";
// Wait this long after an edit before fetching new captions, in ms.
const CAPTIONS_WAIT_MS = 300;
// libass sizes a font by its line height, about 1.15 em for Arial, where
// the canvas sizes it by the em; checked against a draft's captions.
const ASS_FONT_SCALE = 0.87;
// The blurred fill is drawn this many times smaller, then scaled up.
const BLUR_SHRINK = 24;

const blurCanvas = document.createElement("canvas");
let captionChunks = []; // [[start, end, text], ...] per chunk, source seconds
let captionsKey = "";
let captionsWanted = "";
let captionsChanged = 0;
let captionsPending = false;
let crop = null; // {width, height, points} for the face style, or null
let cropFrame = null; // the source frame size the crop is in
let cropKey = "";
let cropPending = false;
let previewFrame = 0; // the animation frame request, while the preview shows

// --- What to draw: captions and crop for the current span ---

function cutsQuery() {
  return activeCuts()
    .map(([start, end]) => `${start}:${end}`)
    .join(",");
}

function spanKey() {
  return `${current.key}|${span.start}|${span.end}|${cutsQuery()}`;
}

async function fetchCaptions(key) {
  captionsPending = true;
  const cuts = cutsQuery();
  const query = `start=${span.start}&end=${span.end}${cuts ? `&cuts=${cuts}` : ""}`;
  try {
    const response = await fetch(`/api/captions?${query}`);
    const data = await response.json().catch(() => ({}));
    captionChunks = response.ok ? data.chunks : [];
  } catch {
    captionChunks = [];
  } finally {
    captionsKey = key;
    captionsPending = false;
  }
}

// The face style's crop for the span, from the server's face tracker.
async function fetchCrop(key) {
  cropPending = true;
  crop = null;
  showNote("Tracking faces…");
  try {
    const response = await fetch(`/api/crop?start=${span.start}&end=${span.end}`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `the server answered ${response.status}`);
    }
    crop = data.crop;
    cropFrame = data.frame || null;
    showNote(data.note || "");
  } catch (error) {
    showNote(`Framed as fit: ${error.message}`);
  } finally {
    cropKey = key;
    cropPending = false;
  }
}

function showNote(message) {
  previewNote.textContent = message;
  previewNote.hidden = !message;
}

// Fetch what changed: captions a moment after the last edit, the crop
// when the face style's span changed.
function refreshData() {
  const key = spanKey();
  if (burnsCaptions && key !== captionsKey && !captionsPending) {
    if (key !== captionsWanted) {
      captionsWanted = key;
      captionsChanged = performance.now();
    } else if (performance.now() - captionsChanged >= CAPTIONS_WAIT_MS) {
      fetchCaptions(key);
    }
  }
  const face = isVertical && chosenStyle(current) === "face";
  const faceKey = face ? `${current.key}|${span.start}|${span.end}` : "";
  if (faceKey !== cropKey && !cropPending) {
    if (face) {
      fetchCrop(faceKey);
    } else {
      crop = null;
      cropKey = "";
      showNote("");
    }
  }
}

// --- Time in the finished clip ---

// Seconds into the finished clip at source time `seconds`: from the span's
// start, less the cuts before it (ADR 0029).
function clipSeconds(seconds) {
  let removed = 0;
  for (const [start, end] of activeCuts()) {
    if (seconds >= end) {
      removed += end - start;
    } else if (seconds > start) {
      removed += seconds - start;
    }
  }
  return seconds - span.start - removed;
}

function clipLength() {
  return clipSeconds(span.end);
}

// --- Drawing ---

function canvasSize() {
  const box = previewView.getBoundingClientRect();
  const [wide, high] = isVertical ? [9, 16] : [16, 9];
  let height = box.height;
  let width = (height * wide) / high;
  if (width > box.width) {
    width = box.width;
    height = (width * high) / wide;
  }
  return [Math.max(Math.round(width), 1), Math.max(Math.round(height), 1)];
}

function drawFit(context, width, height) {
  const sourceWidth = player.videoWidth;
  const sourceHeight = player.videoHeight;
  if (!isVertical) {
    const scale = Math.min(width / sourceWidth, height / sourceHeight);
    const w = sourceWidth * scale;
    const h = sourceHeight * scale;
    context.drawImage(player, (width - w) / 2, (height - h) / 2, w, h);
    return;
  }
  // The fill: the frame covering the canvas, drawn tiny and scaled up.
  const small = [Math.ceil(width / BLUR_SHRINK), Math.ceil(height / BLUR_SHRINK)];
  blurCanvas.width = small[0];
  blurCanvas.height = small[1];
  const cover = Math.max(small[0] / sourceWidth, small[1] / sourceHeight);
  const blur = blurCanvas.getContext("2d");
  blur.drawImage(
    player,
    (small[0] - sourceWidth * cover) / 2,
    (small[1] - sourceHeight * cover) / 2,
    sourceWidth * cover,
    sourceHeight * cover,
  );
  context.imageSmoothingEnabled = true;
  context.imageSmoothingQuality = "high";
  context.drawImage(blurCanvas, 0, 0, width, height);
  // The picture across the width, centred (ADR 0016).
  const h = (width * sourceHeight) / sourceWidth;
  context.drawImage(player, 0, (height - h) / 2, width, h);
}

function cropLeft(seconds) {
  const at = seconds - span.start;
  let left = crop.points[0][1];
  for (const [time, x] of crop.points) {
    if (time <= at) {
      left = x;
    }
  }
  return left;
}

function drawFace(context, width, height, seconds) {
  // The crop is in the probed frame's pixels; the player's may differ.
  const scale = cropFrame ? player.videoWidth / cropFrame[0] : 1;
  context.drawImage(
    player,
    cropLeft(seconds) * scale,
    0,
    crop.width * scale,
    crop.height * scale,
    0,
    0,
    width,
    height,
  );
}

// Lines of words no wider than `maxWidth` at the context's font.
function wrapWords(context, words, maxWidth) {
  const lines = [];
  let line = [];
  for (const word of words) {
    const trial = [...line, word];
    if (line.length && context.measureText(trial.map((w) => w.text).join(" ")).width > maxWidth) {
      lines.push(line);
      line = [word];
    } else {
      line = trial;
    }
  }
  if (line.length) {
    lines.push(line);
  }
  return lines;
}

// The chunk shown at `seconds` and its active word, as build_ass times
// them: each word until the next starts, the last until it ends.
function activeChunk(seconds) {
  for (const chunk of captionChunks) {
    for (let i = 0; i < chunk.length; i += 1) {
      const end = i + 1 < chunk.length ? chunk[i + 1][0] : chunk[i][1];
      if (seconds >= chunk[i][0] && seconds < end) {
        return [chunk, i];
      }
    }
  }
  return null;
}

function drawCaptions(context, width, height, seconds) {
  const shown = activeChunk(seconds);
  if (!shown) {
    return;
  }
  const [chunk, active] = shown;
  const scale = height / previewLayout.height;
  const size = previewLayout.caption_size * ASS_FONT_SCALE * scale;
  context.font = `bold ${size}px Arial, sans-serif`;
  context.textBaseline = "alphabetic";
  context.lineJoin = "round";
  context.lineWidth = previewLayout.caption_outline * 2 * scale;
  context.strokeStyle = "#000";
  const words = chunk.map(([, , text], index) => ({ text, active: index === active }));
  const lines = wrapWords(context, words, width - previewLayout.caption_margin_h * 2 * scale);
  const space = context.measureText(" ").width;
  let baseline = height - previewLayout.caption_margin_v * scale - (lines.length - 1) * size;
  for (const line of lines) {
    const lineWidth = context.measureText(line.map((w) => w.text).join(" ")).width;
    let x = (width - lineWidth) / 2;
    for (const word of line) {
      context.strokeText(word.text, x, baseline);
      context.fillStyle = word.active ? HIGHLIGHT : "#fff";
      context.fillText(word.text, x, baseline);
      x += context.measureText(word.text).width + space;
    }
    baseline += size;
  }
}

// On-screen texts (ADR 0030) at the finished clip's time, each on one
// half-dark box behind all its lines (the one box amendment).
function drawOnScreenTexts(context, width, height, seconds) {
  const at = clipSeconds(seconds);
  const scale = height / previewLayout.height;
  const size = previewLayout.text_size * ASS_FONT_SCALE * scale;
  const padding = previewLayout.text_padding * scale;
  context.font = `bold ${size}px Arial, sans-serif`;
  context.textBaseline = "middle";
  for (const item of sentTexts(current)) {
    if (!(at >= item.start && at < Math.min(item.end, clipLength()))) {
      continue;
    }
    const words = item.text.split(/\s+/).filter(Boolean).map((text) => ({ text }));
    const lines = wrapWords(context, words, width - previewLayout.caption_margin_h * 2 * scale)
      .map((line) => line.map((w) => w.text).join(" "));
    const lineHeight = size * 1.15;
    const boxWidth = Math.max(...lines.map((line) => context.measureText(line).width)) + padding * 2;
    const boxHeight = lines.length * lineHeight + padding * 2;
    const [x, y] = (item.place === "top" ? previewLayout.text_top : previewLayout.text_middle)
      .map((value) => value * scale);
    const top = item.place === "top" ? y : y - boxHeight / 2;
    context.fillStyle = "rgb(0 0 0 / 0.5)";
    context.fillRect(x - boxWidth / 2, top, boxWidth, boxHeight);
    context.fillStyle = "#fff";
    lines.forEach((line, index) => {
      const lineWidth = context.measureText(line).width;
      context.fillText(line, x - lineWidth / 2, top + padding + lineHeight * (index + 0.5));
    });
  }
}

function drawPreview() {
  if (!current || !span || !previewLayout || shownScreen() !== "preview") {
    return;
  }
  refreshData();
  const [width, height] = canvasSize();
  const ratio = window.devicePixelRatio || 1;
  if (previewCanvas.width !== Math.round(width * ratio)) {
    previewCanvas.width = Math.round(width * ratio);
    previewCanvas.height = Math.round(height * ratio);
    previewCanvas.style.width = `${width}px`;
    previewCanvas.style.height = `${height}px`;
  }
  const context = previewCanvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.fillStyle = "#000";
  context.fillRect(0, 0, width, height);
  const seconds = player.currentTime;
  if (player.videoWidth) {
    if (crop && isVertical) {
      drawFace(context, width, height, seconds);
    } else {
      drawFit(context, width, height);
    }
  }
  if (burnsCaptions) {
    drawCaptions(context, width, height, seconds);
  }
  drawOnScreenTexts(context, width, height, seconds);
  const inside = seconds >= span.start && seconds <= span.end;
  previewTime.textContent = inside
    ? `${clipSeconds(seconds).toFixed(1)} / ${clipLength().toFixed(1)} s`
    : `Outside the span (${fmtTenths(seconds)})`;
}

// Draws every frame while the preview shows; cheap at the shown size.
function previewLoop() {
  drawPreview();
  previewFrame = shownScreen() === "preview" ? requestAnimationFrame(previewLoop) : 0;
}

// Called by drawScreen whenever the view changes.
function drawPreviewNow() {
  if (shownScreen() === "preview" && !previewFrame) {
    previewFrame = requestAnimationFrame(previewLoop);
  }
}

// Play the span through its cuts from the playhead, or from the start
// handle when the playhead is outside it; pause when playing.
function togglePreviewPlay() {
  if (!player.paused) {
    player.pause();
    return;
  }
  const at = player.currentTime;
  if (at < span.start || at >= span.end - 0.05) {
    document.getElementById("preview").click();
    return;
  }
  preview = { end: span.end, settling: false };
  player.play().catch(() => {
    preview = null;
  });
  requestAnimationFrame(watchPreview);
}

previewCanvas.addEventListener("click", togglePreviewPlay);
drawPreviewNow();
