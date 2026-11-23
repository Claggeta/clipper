// The cam box set by hand (ADR 0025, 12b amendment; V8 item 12b): a box
// dragged over the cam on the source player, kept for the folder and maybe
// the channel, which makes facecam a style for every clip. The box is in
// fractions of the video's frame; the server lays out the facecam frame.
// Loaded after frame.js and before preview.js.
"use strict";

const camboxStatus = document.getElementById("cambox-status");
const camboxDraw = document.getElementById("cambox-draw");
const camboxClear = document.getElementById("cambox-clear");
const camboxKeepRow = document.getElementById("cambox-keep-row");
const camboxKeep = document.getElementById("cambox-keep");
const camboxLayer = document.getElementById("cambox-layer");
const camboxRect = document.getElementById("cambox-rect");
const camboxSave = document.getElementById("cambox-save");
// The server's least box, as a share of the frame each way.
const MIN_CAM_SIDE = 0.05;

let camDraft = null; // {x, y, w, h} while drawing, else null
let camDrag = null; // {mode: "draw" | "move", x, y, from} during a drag

function drawCamBoxPanel() {
  const state = folderCamBox || {};
  // yt-dlp gave one channel as "Lacy " with a trailing space (ADR 0018).
  const channel = state.channel && state.channel.trim();
  const where = [];
  if (state.folder) {
    where.push("set for this folder");
  }
  if (state.saved) {
    where.push(`saved for ${channel}`);
  }
  camboxStatus.textContent = state.box
    ? `${where.join("; ")}. Facecam is a style for every clip.`.replace(/^./, (c) => c.toUpperCase())
    : "None. Draw one over the cam to use the facecam style on every clip.";
  camboxDraw.disabled = !isVertical || busy;
  camboxDraw.textContent = state.box ? "Redraw cam box" : "Draw cam box";
  camboxClear.disabled = !state.box || busy;
  camboxKeepRow.hidden = !channel;
  document.getElementById("cambox-channel").textContent = channel || "";
}

// Where the picture sits inside the player, which letterboxes it.
function pictureRect() {
  const width = player.clientWidth;
  const height = player.clientHeight;
  const ratio = player.videoWidth && player.videoHeight ? player.videoWidth / player.videoHeight : 16 / 9;
  const w = Math.min(width, height * ratio);
  const h = w / ratio;
  return { left: player.offsetLeft + (width - w) / 2, top: player.offsetTop + (height - h) / 2, w, h };
}

function placeCamLayer() {
  const picture = pictureRect();
  Object.assign(camboxLayer.style, {
    left: `${picture.left}px`,
    top: `${picture.top}px`,
    width: `${picture.w}px`,
    height: `${picture.h}px`,
  });
  camboxRect.hidden = !camDraft;
  if (camDraft) {
    Object.assign(camboxRect.style, {
      left: `${camDraft.x * 100}%`,
      top: `${camDraft.y * 100}%`,
      width: `${camDraft.w * 100}%`,
      height: `${camDraft.h * 100}%`,
    });
  }
  camboxSave.disabled = !camDraft || camDraft.w < MIN_CAM_SIDE || camDraft.h < MIN_CAM_SIDE;
}

function startCamDraw() {
  showScreen("source");
  player.pause();
  const box = folderCamBox && folderCamBox.box;
  camDraft = box ? { x: box.x, y: box.y, w: box.w, h: box.h } : null;
  camboxLayer.hidden = false;
  placeCamLayer();
}

function stopCamDraw() {
  camDraft = null;
  camDrag = null;
  camboxLayer.hidden = true;
}

// The pointer as fractions of the picture, kept inside it.
function camPoint(event) {
  const rect = camboxLayer.getBoundingClientRect();
  return {
    x: clamp((event.clientX - rect.left) / rect.width, 0, 1),
    y: clamp((event.clientY - rect.top) / rect.height, 0, 1),
  };
}

function insideCamDraft(point) {
  return (
    camDraft &&
    point.x >= camDraft.x &&
    point.x <= camDraft.x + camDraft.w &&
    point.y >= camDraft.y &&
    point.y <= camDraft.y + camDraft.h
  );
}

camboxLayer.addEventListener("pointerdown", (event) => {
  if (event.target.closest(".cambox-bar")) {
    return;
  }
  const point = camPoint(event);
  camDrag = insideCamDraft(point)
    ? { mode: "move", ...point, from: { ...camDraft } }
    : { mode: "draw", ...point };
  camboxLayer.setPointerCapture(event.pointerId);
  event.preventDefault();
});

camboxLayer.addEventListener("pointermove", (event) => {
  if (!camDrag) {
    return;
  }
  const point = camPoint(event);
  if (camDrag.mode === "move") {
    const { from } = camDrag;
    camDraft = {
      ...from,
      x: clamp(from.x + point.x - camDrag.x, 0, 1 - from.w),
      y: clamp(from.y + point.y - camDrag.y, 0, 1 - from.h),
    };
  } else {
    camDraft = {
      x: Math.min(camDrag.x, point.x),
      y: Math.min(camDrag.y, point.y),
      w: Math.abs(point.x - camDrag.x),
      h: Math.abs(point.y - camDrag.y),
    };
  }
  placeCamLayer();
});

camboxLayer.addEventListener("pointerup", () => {
  camDrag = null;
});

// The server's reply is the whole clips payload: the box, and each clip's
// facecam drafts framed on it.
function applyCamBox(reply) {
  folderCamBox = reply.cam_box || null;
  for (const updated of reply.clips || []) {
    for (const clip of clipsByKey.values()) {
      if (clip.rank === updated.rank) {
        clip.variants = updated.variants;
      }
    }
  }
  drawCamBoxPanel();
  if (current) {
    drawDraft(current);
    drawFrameEditor(current);
  }
  if (typeof drawPreviewNow === "function") {
    drawPreviewNow();
  }
}

async function sendCamBox(box, forChannel) {
  busy = true;
  drawCamBoxPanel();
  try {
    applyCamBox(await post("/api/cam-box", { box, channel: forChannel }));
    return true;
  } catch (error) {
    notice(`Cam box not saved: ${error.message}`);
    return false;
  } finally {
    busy = false;
    drawCamBoxPanel();
  }
}

camboxDraw.addEventListener("click", startCamDraw);
document.getElementById("cambox-cancel").addEventListener("click", stopCamDraw);

camboxSave.addEventListener("click", async () => {
  // Kept to four places, as the cams file writes it.
  const round4 = (value) => Math.round(value * 10000) / 10000;
  const box = {
    x: round4(camDraft.x),
    y: round4(camDraft.y),
    w: Math.min(round4(camDraft.w), 1 - round4(camDraft.x)),
    h: Math.min(round4(camDraft.h), 1 - round4(camDraft.y)),
  };
  const forChannel = Boolean(folderCamBox && folderCamBox.channel && camboxKeep.checked);
  if (await sendCamBox(box, forChannel)) {
    stopCamDraw();
    // Show the clip in the new frame, ready to Save.
    if (current) {
      chosen.set(current.key, "facecam");
      drawDraft(current);
      drawFrameEditor(current);
      drawUnsaved();
      showScreen("preview");
    }
    notice("Cam box saved. Save a clip in facecam to render it.");
  }
});

camboxClear.addEventListener("click", async () => {
  const state = folderCamBox || {};
  // A box only the channel keeps is cleared there, or nothing would change.
  const forChannel = Boolean(state.channel && (camboxKeep.checked || (state.saved && !state.folder)));
  if (await sendCamBox(null, forChannel)) {
    for (const [key, style] of chosen) {
      const clip = clipsByKey.get(key);
      if (style === "facecam" && clip && !facecamDraft(clip)) {
        chosen.delete(key);
      }
    }
    if (current) {
      drawDraft(current);
      drawFrameEditor(current);
      drawUnsaved();
    }
    notice("Cam box cleared.");
  }
});

// Esc cancels drawing, before the page's other keys see it.
document.addEventListener(
  "keydown",
  (event) => {
    if (!camboxLayer.hidden && event.key === "Escape") {
      stopCamDraw();
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  },
  true,
);

window.addEventListener("resize", () => {
  if (!camboxLayer.hidden) {
    placeCamLayer();
  }
});

loaded.then(drawCamBoxPanel);
