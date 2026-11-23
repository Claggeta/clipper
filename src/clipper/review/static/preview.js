// The live preview (ADR 0031, V7 item 9e): the chosen clip as it will
// look, drawn on a canvas from the source player, which plays behind it.
// The span plays through its cuts; framed as fit (the picture across the
// width over a blurred copy) or face (the crop a re-render would use);
// with the word captions and on-screen texts drawn at their burn-in
// sizes. Close, not exact: Save's re-render is the result. Loaded after
// app.js, keys.js, precision.js, captions.js, frame.js, audio.js and
// overlays.js.
"use strict";

const previewCanvas = document.getElementById("preview-canvas");
const previewTime = document.getElementById("preview-time");
const previewNote = document.getElementById("preview-note");
// Wait this long after an edit before fetching new captions, in ms.
const CAPTIONS_WAIT_MS = 300;
// libass sizes a font by its line height, about 1.15 em for Arial, where
// the canvas sizes it by the em; checked against a draft's captions.
const ASS_FONT_SCALE = 0.87;
// The blurred fill is drawn this many times smaller, then scaled up.
const BLUR_SHRINK = 24;

const blurCanvas = document.createElement("canvas");
// [[start, end, text, key], ...] per chunk, source seconds
let captionChunks = [];
let captionsKey = "";
let captionsWanted = "";
let captionsEditedAt = 0;
let captionsPending = false;
let crop = null; // {width, height, points} for the face style, or null
let cropFrame = null; // the source frame size the crop is in
let cropKey = "";
let cropPending = false;
let previewFrame = 0; // the animation frame request, while the preview shows
// Where each on-screen text was last drawn, in the canvas's CSS pixels,
// for dragging: [{index, left, top, width, height}].
let textBoxes = [];
// A press on the canvas is a drag past this many pixels, else a click.
const TEXT_DRAG_PX = 4;
// A dragged text's centre snaps to the middle within this fraction.
const TEXT_SNAP = 0.02;

// --- What to draw: captions and crop for the current span ---

function cutsQuery() {
  return activeCuts()
    .map(([start, end]) => `${start}:${end}`)
    .join(",");
}

// The source range the captions are wanted for: the span, or every
// block of a clip's own sequence (ADR 0034); chunks are in source seconds.
function captionRange() {
  if (!ownSequence(current)) {
    return [span.start, span.end];
  }
  const blocks = blocksOf(current);
  return [Math.min(...blocks.map((b) => b.start)), Math.max(...blocks.map((b) => b.end))];
}

function spanKey() {
  const [start, end] = captionRange();
  return `${current.key}|${start}|${end}|${cutsQuery()}|${captionEditsQuery(current)}`;
}

async function fetchCaptions(key) {
  captionsPending = true;
  const cuts = cutsQuery();
  const [start, end] = captionRange();
  const query =
    `start=${start}&end=${end}${cuts ? `&cuts=${cuts}` : ""}` +
    captionEditsQuery(current);
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
      captionsEditedAt = performance.now();
    } else if (performance.now() - captionsEditedAt >= CAPTIONS_WAIT_MS) {
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
// start, less the cuts before it (ADR 0029); in a clip with its own
// sequence, in the block playing (ADR 0034), NaN outside every block.
function clipSeconds(seconds) {
  if (ownSequence(current)) {
    return sequenceClipTime(seconds);
  }
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
  return ownSequence(current) ? sequenceLength() : clipSeconds(span.end);
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

// `media` is the source player, or a B-roll insert's video or still
// (ADR 0034), fitted inside the frame as the renderer fits it.
function drawFit(context, width, height, media = player, size = null) {
  const sourceWidth = size ? size[0] : player.videoWidth;
  const sourceHeight = size ? size[1] : player.videoHeight;
  if (!isVertical) {
    const scale = Math.min(width / sourceWidth, height / sourceHeight);
    const w = sourceWidth * scale;
    const h = sourceHeight * scale;
    if (media !== player) {
      context.fillStyle = "#000";
      context.fillRect(0, 0, width, height);
    }
    context.drawImage(media, (width - w) / 2, (height - h) / 2, w, h);
    return;
  }
  // The fill: the frame covering the canvas, drawn tiny and scaled up.
  const small = [Math.ceil(width / BLUR_SHRINK), Math.ceil(height / BLUR_SHRINK)];
  blurCanvas.width = small[0];
  blurCanvas.height = small[1];
  const cover = Math.max(small[0] / sourceWidth, small[1] / sourceHeight);
  const blur = blurCanvas.getContext("2d");
  blur.drawImage(
    media,
    (small[0] - sourceWidth * cover) / 2,
    (small[1] - sourceHeight * cover) / 2,
    sourceWidth * cover,
    sourceHeight * cover,
  );
  context.imageSmoothingEnabled = true;
  context.imageSmoothingQuality = "high";
  context.drawImage(blurCanvas, 0, 0, width, height);
  // The picture fitted inside the frame, centred (ADR 0016): across the
  // width for a landscape source, the height for a tall insert.
  const fit = Math.min(width / sourceWidth, height / sourceHeight);
  const w = sourceWidth * fit;
  const h = sourceHeight * fit;
  context.drawImage(media, (width - w) / 2, (height - h) / 2, w, h);
}

// The facecam frame for the cam picked, in the probed frame's pixels, or
// null when the style is not facecam (ADR 0025, facecam style amendment);
// from the cam box set by hand when there is one (12b amendment).
function facecamFrame(clip) {
  const found = isVertical && chosenStyle(clip) === "facecam" ? facecamLayouts(clip) : null;
  if (!found) {
    return null;
  }
  const cam = camOf(clip);
  return { ...found.layouts[cam], frame: found.frame, position: cam };
}

// The cam box across the width at its own shape, over (or under) a centre
// crop of the gameplay, as the renderer stacks them.
function drawFacecam(context, width, height, frame) {
  const scale = player.videoWidth / frame.frame[0];
  const camHeight = (frame.cam_height / 1920) * height;
  const top = frame.position === "top";
  const [cx, cy, cw, ch] = frame.cam;
  const [gx, gy, gw, gh] = frame.game;
  context.drawImage(
    player,
    cx * scale,
    cy * scale,
    cw * scale,
    ch * scale,
    0,
    top ? 0 : height - camHeight,
    width,
    camHeight,
  );
  context.drawImage(
    player,
    gx * scale,
    gy * scale,
    gw * scale,
    gh * scale,
    0,
    top ? camHeight : 0,
    width,
    height - camHeight,
  );
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

// The full-height 9:16 window of a hand crop, in the player's pixels
// (ADR 0025, hand crop amendment; core/framing.py's hand_left).
function handWindow(hand) {
  const sourceWidth = player.videoWidth;
  const sourceHeight = player.videoHeight;
  const window = Math.floor(Math.min(Math.round((sourceHeight * 9) / 16), sourceWidth) / 2) * 2;
  const left = clamp(Math.round(hand.x * sourceWidth - window / 2), 0, sourceWidth - window);
  return { left, window, sourceWidth, sourceHeight };
}

function drawHand(context, width, height, hand) {
  const { left, window, sourceHeight } = handWindow(hand);
  context.drawImage(player, left, 0, window, sourceHeight, 0, 0, width, height);
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

// As libass's smart wrapping (WrapStyle 0): as few lines as greedy
// wrapping needs, but as even as they can be, the top one the wider.
function balancedWrap(context, words, maxWidth) {
  const lines = wrapWords(context, words, maxWidth);
  if (lines.length < 2) {
    return lines;
  }
  let low = 0;
  let high = maxWidth;
  for (let step = 0; step < 16; step += 1) {
    const middle = (low + high) / 2;
    if (wrapWords(context, words, middle).length > lines.length) {
      low = middle;
    } else {
      high = middle;
    }
  }
  return wrapWords(context, words, high);
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
  // The active word and key words take the clip's colour (ADR 0017).
  const colour = previewLayout.highlights[editsOf(current).highlight];
  const words = chunk.map(([, , text, key], index) => ({
    text,
    lit: index === active || key,
  }));
  const lines = balancedWrap(context, words, width - previewLayout.caption_margin_h * 2 * scale);
  const space = context.measureText(" ").width;
  // Over a cam at the bottom, captions sit above it (ADR 0025).
  const facecam = facecamFrame(current);
  const marginV = (facecam && facecam.caption_margin_v) || previewLayout.caption_margin_v;
  let baseline = height - marginV * scale - (lines.length - 1) * size;
  for (const line of lines) {
    const lineWidth = context.measureText(line.map((w) => w.text).join(" ")).width;
    let x = (width - lineWidth) / 2;
    for (const word of line) {
      context.strokeText(word.text, x, baseline);
      context.fillStyle = word.lit ? colour : "#fff";
      context.fillText(word.text, x, baseline);
      x += context.measureText(word.text).width + space;
    }
    baseline += size;
  }
}

// On-screen texts (ADR 0030) at the finished clip's time, in their look:
// a colour, one half-dark box behind all lines or a black outline, a
// size, a fade in, and their place or own position (styling amendment).
function drawOnScreenTexts(context, width, height, seconds) {
  const at = clipSeconds(seconds);
  const scale = height / previewLayout.height;
  const padding = previewLayout.text_padding * scale;
  context.textBaseline = "middle";
  context.lineJoin = "round";
  textBoxes = [];
  textsOf(current).forEach((item, index) => {
    const text = item.text.trim();
    if (!text || !(at >= item.start && at < Math.min(item.end, clipLength()))) {
      return;
    }
    const size =
      previewLayout.text_size *
      previewLayout.text_sizes[item.size || "large"] *
      ASS_FONT_SCALE *
      scale;
    context.font = `bold ${size}px Arial, sans-serif`;
    const words = text.split(/\s+/).map((word) => ({ text: word }));
    const lines = balancedWrap(context, words, width - previewLayout.caption_margin_h * 2 * scale)
      .map((line) => line.map((w) => w.text).join(" "));
    const lineHeight = size * 1.15;
    const boxWidth = Math.max(...lines.map((line) => context.measureText(line).width)) + padding * 2;
    const boxHeight = lines.length * lineHeight + padding * 2;
    let x;
    let top;
    if (item.x !== null && item.x !== undefined) {
      x = item.x * width;
      top = item.y * height - boxHeight / 2;
    } else {
      const point = item.place === "top" ? previewLayout.text_top : previewLayout.text_middle;
      x = point[0] * scale;
      top = item.place === "top" ? point[1] * scale : point[1] * scale - boxHeight / 2;
    }
    const fadeSeconds = previewLayout.text_fade_ms / 1000;
    context.globalAlpha = item.fade ? Math.min((at - item.start) / fadeSeconds, 1) : 1;
    const outline = item.look === "outline";
    if (!outline) {
      context.fillStyle = "rgb(0 0 0 / 0.5)";
      context.fillRect(x - boxWidth / 2, top, boxWidth, boxHeight);
    }
    context.fillStyle = previewLayout.text_colours[item.colour || "white"];
    context.strokeStyle = "#000";
    context.lineWidth = previewLayout.text_outline * 2 * scale;
    lines.forEach((line, lineIndex) => {
      const left = x - context.measureText(line).width / 2;
      const middle = top + padding + lineHeight * (lineIndex + 0.5);
      if (outline) {
        context.strokeText(line, left, middle);
      }
      context.fillText(line, left, middle);
    });
    context.globalAlpha = 1;
    textBoxes.push({ index, left: x - boxWidth / 2, top, width: boxWidth, height: boxHeight });
  });
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
  const shown = insert ? insertFrame() : null;
  if (insert) {
    // A B-roll insert, fitted on its blurred copy; no captions or zooms
    // land on it (ADR 0034, B-roll amendment).
    if (shown) {
      drawFit(context, width, height, shown.media, [shown.width, shown.height]);
    }
  } else if (player.videoWidth && facecamFrame(current)) {
    drawFacecam(context, width, height, facecamFrame(current));
  } else if (player.videoWidth) {
    // A zoom scales the frame around its centre, as the renderer does:
    // scaled by Z, then cropped at (Z - 1) times its centre (ADR 0016).
    const zoomed = isVertical ? activeZoom(seconds) : null;
    if (zoomed) {
      const z = 1 + (zoomed.zoom.scale - 1) * zoomed.weight;
      const left = width * (zoomed.zoom.scale - 1) * zoomed.zoom.x * zoomed.weight;
      const top = height * (zoomed.zoom.scale - 1) * zoomed.zoom.y * zoomed.weight;
      context.setTransform(ratio * z, 0, 0, ratio * z, -ratio * left, -ratio * top);
    }
    const hand = isVertical ? activeHand(seconds) : null;
    if (hand) {
      drawHand(context, width, height, hand);
    } else if (crop && isVertical) {
      drawFace(context, width, height, seconds);
    } else {
      drawFit(context, width, height);
    }
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
  }
  // B-roll overlays in clip time, over inserts too (ADR 0034).
  drawOverlays(context, width, height, clipSeconds(seconds), !player.paused || insertPlaying());
  if (burnsCaptions && !insert) {
    drawCaptions(context, width, height, seconds);
  }
  drawOnScreenTexts(context, width, height, seconds);
  const inside = ownSequence(current)
    ? Number.isFinite(clipSeconds(seconds))
    : seconds >= span.start && seconds <= span.end;
  previewTime.textContent = inside
    ? `${clipSeconds(seconds).toFixed(1)} / ${clipLength().toFixed(1)} s`
    : `Outside the ${ownSequence(current) ? "sequence" : "span"} (${fmtTenths(seconds)})`;
}

// Draws every frame while the preview shows; cheap at the shown size.
function previewLoop() {
  drawPreview();
  syncPreviewAudio();
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
  if (insertPlaying()) {
    pauseInsert();
    return;
  }
  if (!player.paused) {
    player.pause();
    return;
  }
  if (ownSequence(current)) {
    playSequence(insert ? insert.index : null);
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

// A press on a text drags it to a position of its own (ADR 0030, styling
// amendment); a press anywhere else, or one that does not move, plays or
// pauses.
previewCanvas.addEventListener("pointerdown", (event) => {
  if (event.button !== 0 || !current) {
    return;
  }
  const box = previewCanvas.getBoundingClientRect();
  const px = event.clientX - box.left;
  const py = event.clientY - box.top;
  const hit = [...textBoxes]
    .reverse()
    .find((b) => px >= b.left && px <= b.left + b.width && py >= b.top && py <= b.top + b.height);
  // Off a text, a picture-in-picture box moves (ADR 0034, B-roll amendment).
  const boxHit = hit
    ? null
    : [...overlayBoxes]
        .reverse()
        .find((b) => px >= b.left && px <= b.left + b.width && py >= b.top && py <= b.top + b.height);
  const placed = boxHit || hit;
  const item = hit ? textsOf(current)[hit.index] : boxHit ? overlaysOf(current)[boxHit.index] : null;
  const offset = placed ? [px - (placed.left + placed.width / 2), py - (placed.top + placed.height / 2)] : null;
  if (boxHit) {
    selectedOverlay = boxHit.index;
    drawOverlayLane();
  }
  // Off both, a drag pans the zoom in force, else the hand crop's window
  // (ADR 0016 and 0025 amendments): the picture follows the pointer.
  const zoomed = item || !isVertical ? null : activeZoom(player.currentTime);
  const hand = item || zoomed || !isVertical ? null : activeHand(player.currentTime);
  const start = zoomed
    ? [zoomed.zoom.x, zoomed.zoom.y]
    : hand
      ? [hand.x, 0]
      : null;
  let dragging = false;
  previewCanvas.setPointerCapture(event.pointerId);
  const onMove = (moveEvent) => {
    if (!item && !start) {
      return;
    }
    if (!dragging && Math.hypot(moveEvent.clientX - event.clientX, moveEvent.clientY - event.clientY) < TEXT_DRAG_PX) {
      return;
    }
    dragging = true;
    const dx = moveEvent.clientX - event.clientX;
    const dy = moveEvent.clientY - event.clientY;
    if (zoomed) {
      const spare = zoomed.zoom.scale - 1;
      zoomed.zoom.x = clamp(start[0] - dx / (box.width * spare), 0, 1);
      zoomed.zoom.y = clamp(start[1] - dy / (box.height * spare), 0, 1);
      frameChanged();
      return;
    }
    if (hand) {
      const { window, sourceWidth } = handWindow(hand);
      hand.x = clamp(start[0] - (dx * window) / (box.width * sourceWidth), 0, 1);
      frameChanged();
      return;
    }
    let x = (moveEvent.clientX - box.left - offset[0]) / box.width;
    const y = (moveEvent.clientY - box.top - offset[1]) / box.height;
    if (Math.abs(x - 0.5) < TEXT_SNAP) {
      x = 0.5;
    }
    item.x = Math.round(clamp(x, 0, 1) * 1000) / 1000;
    item.y = Math.round(clamp(y, 0, 1) * 1000) / 1000;
    if (boxHit) {
      drawUnsaved();
    } else {
      textsChanged();
    }
  };
  const onUp = () => {
    previewCanvas.removeEventListener("pointermove", onMove);
    previewCanvas.removeEventListener("pointerup", onUp);
    previewCanvas.removeEventListener("pointercancel", onUp);
    if (dragging && boxHit) {
      overlaysChanged();
    } else if (dragging && item) {
      drawTexts(current);
    } else if (dragging) {
      drawFrameEditor(current);
    } else {
      togglePreviewPlay();
    }
  };
  previewCanvas.addEventListener("pointermove", onMove);
  previewCanvas.addEventListener("pointerup", onUp);
  previewCanvas.addEventListener("pointercancel", onUp);
});
drawPreviewNow();
