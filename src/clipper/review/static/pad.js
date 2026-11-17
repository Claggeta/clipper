// The pad (ADR 0034, V7 item 9n-4): ranges of the source kept for later,
// one list per drafts folder, saved in its manifest. A range goes to the
// pad from the Cut in mark to the playhead (or the span) with Shift+P or
// "To pad", or by a Shift-drag on the strip; a piece goes into the clip's
// sequence by a drag onto the Sequence lane or by Add, as a copy of its
// range. Loaded after app.js and sequence.js.
"use strict";

const padList = document.getElementById("pad-list");
const padNotice = document.getElementById("pad-notice");
let pad = []; // [{id, name, start, end}], as the server keeps it
let padArmed = null; // {id, timer} while × waits for its second click
let padPlaying = null; // the piece playing, until its end
// The MIME type a dragged piece carries, so the lane knows it.
const PIECE_TYPE = "application/x-clipper-piece";

function padSay(message) {
  padNotice.textContent = message;
}

async function padPost(path, body) {
  try {
    const reply = await post(path, body);
    pad = reply.pad;
    padSay("");
    drawPad();
    return true;
  } catch (error) {
    padSay(error.message);
    return false;
  }
}

// Send a range to the pad: Cut in mark to the playhead, else the span.
async function toPad(start = null, end = null) {
  if (!current) {
    return;
  }
  let from = start;
  let to = end;
  if (from === null) {
    if (cutIn && cutIn.key === current.key) {
      [from, to] = [cutIn.seconds, player.currentTime].sort((a, b) => a - b);
      cutIn = null;
      drawMarks();
    } else {
      [from, to] = [span.start, span.end];
    }
  }
  if (await padPost("/api/pad", { start: round(from), end: round(to) })) {
    notice("");
    showBin("pad");
  }
}

function drawPad() {
  padList.replaceChildren();
  document.getElementById("pad-empty").hidden = pad.length > 0;
  for (const piece of pad) {
    const item = document.createElement("li");
    item.className = "piece";
    item.draggable = true;
    item.dataset.id = piece.id;
    const thumb = document.createElement("img");
    thumb.className = "piece-thumb";
    thumb.alt = "";
    thumb.src = `/frames/${framesKey}/${Math.floor(piece.start)}.jpg`;
    thumb.addEventListener("error", () => thumb.remove());
    const name = document.createElement("input");
    name.className = "piece-name";
    name.value = piece.name;
    name.maxLength = 60;
    name.title = "Click to rename; Enter keeps it";
    const rename = () => {
      const value = name.value.trim();
      if (value && value !== piece.name) {
        padPost(`/api/pad/${piece.id}`, { name: value });
      } else {
        name.value = piece.name;
      }
    };
    name.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        name.blur();
      }
    });
    name.addEventListener("change", rename);
    const meta = document.createElement("span");
    meta.className = "piece-meta";
    meta.textContent = `${fmtTenths(piece.start)} - ${fmtTenths(piece.end)} · ${(piece.end - piece.start).toFixed(1)} s`;
    const actions = document.createElement("span");
    actions.className = "piece-actions";
    const play = document.createElement("button");
    play.type = "button";
    play.textContent = "Play";
    play.addEventListener("click", () => playPiece(piece));
    const add = document.createElement("button");
    add.type = "button";
    add.textContent = "Add";
    add.title = "Put it in the clip's sequence after the selected block";
    add.addEventListener("click", () => insertPiece(piece, selectedBlock + 1));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "danger";
    remove.textContent = "×";
    remove.title = "Take it off the pad (twice to confirm); clips that use it keep it";
    remove.addEventListener("click", () => removePiece(piece, remove));
    actions.append(play, add, remove);
    item.append(thumb, name, meta, actions);
    item.addEventListener("dragstart", (event) => {
      if (event.target === name) {
        event.preventDefault();
        return;
      }
      event.dataTransfer.setData(PIECE_TYPE, piece.id);
      event.dataTransfer.effectAllowed = "copy";
    });
    padList.append(item);
  }
}

// Clear pad (V8 item 9b): every piece off, one request each, after a second
// press within CONFIRM_MS; clips that use them keep their blocks.
const padClear = document.getElementById("pad-clear");
let clearArmed = null;

padClear.addEventListener("click", async () => {
  if (!pad.length) {
    return;
  }
  if (!clearArmed) {
    padClear.textContent = "Sure?";
    clearArmed = setTimeout(() => {
      clearArmed = null;
      padClear.textContent = "Clear pad";
    }, CONFIRM_MS);
    return;
  }
  clearTimeout(clearArmed);
  clearArmed = null;
  padClear.textContent = "Clear pad";
  for (const piece of [...pad]) {
    if (!(await padPost(`/api/pad/${piece.id}/delete`, {}))) {
      break;
    }
  }
});

// The bin's tabs (V8 item 9b), the open one remembered in this browser.
const BIN_KEY = "clipper-review-bin";

function showBin(name) {
  for (const tab of document.querySelectorAll("[data-bin]")) {
    tab.setAttribute("aria-selected", String(tab.dataset.bin === name));
  }
  for (const panel of document.querySelectorAll("[data-bin-panel]")) {
    panel.hidden = panel.dataset.binPanel !== name;
  }
  try {
    localStorage.setItem(BIN_KEY, name);
  } catch {
    // Storage is blocked; the bin opens on the pad next time.
  }
}

for (const tab of document.querySelectorAll("[data-bin]")) {
  tab.addEventListener("click", () => showBin(tab.dataset.bin));
}

try {
  const kept = localStorage.getItem(BIN_KEY);
  if (kept && document.querySelector(`[data-bin="${kept}"]`)) {
    showBin(kept);
  }
} catch {
  // Storage is blocked; the pad shows.
}

function removePiece(piece, button) {
  if (!padArmed || padArmed.id !== piece.id) {
    if (padArmed) {
      clearTimeout(padArmed.timer);
    }
    button.textContent = "Sure?";
    padArmed = {
      id: piece.id,
      timer: setTimeout(() => {
        padArmed = null;
        drawPad();
      }, CONFIRM_MS),
    };
    return;
  }
  clearTimeout(padArmed.timer);
  padArmed = null;
  padPost(`/api/pad/${piece.id}/delete`, {});
}

// Play just the piece in the source player, from its start to its end.
function playPiece(piece) {
  preview = null;
  padPlaying = piece;
  player.currentTime = piece.start;
  // Watched once it plays: until then the player still reads as paused.
  player
    .play()
    .then(() => requestAnimationFrame(watchPiece))
    .catch(() => {
      padPlaying = null;
    });
}

function watchPiece() {
  if (!padPlaying || player.paused) {
    padPlaying = null;
    return;
  }
  if (player.currentTime >= padPlaying.end) {
    player.pause();
    padPlaying = null;
    return;
  }
  requestAnimationFrame(watchPiece);
}

// A piece into the clip's sequence at `index`, as a block of its own.
function insertPiece(piece, index) {
  if (!current) {
    padSay("Choose a clip first.");
    return;
  }
  const blocks = makeOwn(current);
  const at = clamp(index, 0, blocks.length);
  blocks.splice(at, 0, copyBlock({ start: piece.start, end: piece.end }));
  selectedBlock = at;
  playingBlock = at;
  padSay("");
  sequenceChanged();
}

sequenceLane.addEventListener("dragover", (event) => {
  const types = event.dataTransfer.types;
  if (types.includes(PIECE_TYPE) || types.includes(BROLL_TYPE)) {
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    sequenceLane.classList.add("dropping");
  }
});

sequenceLane.addEventListener("dragleave", () => {
  sequenceLane.classList.remove("dropping");
});

sequenceLane.addEventListener("drop", (event) => {
  sequenceLane.classList.remove("dropping");
  const name = event.dataTransfer.getData(BROLL_TYPE);
  const file = name ? brollFiles.find((f) => f.name === name) : null;
  if (file) {
    event.preventDefault();
    insertBroll(file, dropIndex(event.clientX));
    return;
  }
  const id = event.dataTransfer.getData(PIECE_TYPE);
  const piece = pad.find((p) => p.id === id);
  if (!piece) {
    return;
  }
  event.preventDefault();
  insertPiece(piece, dropIndex(event.clientX));
});

document.getElementById("to-pad").addEventListener("click", () => toPad());

// B-roll files (ADR 0034, B-roll amendment): listed under the pieces,
// inserted after the selected block or dropped onto the Sequence lane, or
// laid over the clip (overlays.js).
const BROLL_TYPE = "application/x-clipper-broll";
const brollList = document.getElementById("broll-list");

function drawBrollFiles() {
  brollList.replaceChildren();
  document.getElementById("broll-empty").hidden = brollFiles.length > 0;
  for (const file of brollFiles) {
    const item = document.createElement("li");
    item.className = "piece broll-file";
    item.draggable = true;
    const src = `/broll/${encodeURIComponent(file.name)}`;
    let thumb;
    if (file.still) {
      thumb = document.createElement("img");
      thumb.src = src;
      thumb.alt = "";
    } else {
      thumb = document.createElement("video");
      thumb.src = `${src}#t=0.1`;
      thumb.muted = true;
      thumb.preload = "metadata";
    }
    thumb.className = "piece-thumb";
    const name = document.createElement("span");
    name.className = "piece-title";
    name.textContent = file.name;
    const meta = document.createElement("span");
    meta.className = "piece-meta";
    const length = file.still ? "still" : file.duration ? `${file.duration.toFixed(1)} s` : "length unknown";
    meta.textContent = `${length}${file.sound ? " · 🔊" : ""}`;
    const actions = document.createElement("span");
    actions.className = "piece-actions";
    const add = document.createElement("button");
    add.type = "button";
    add.textContent = "Insert";
    add.title = "Put it in the clip's sequence after the selected block";
    add.addEventListener("click", () => insertBroll(file, selectedBlock + 1));
    const over = document.createElement("button");
    over.type = "button";
    over.textContent = "Overlay";
    over.title = "Lay it over the clip at the playhead, its sound off";
    over.addEventListener("click", () => addOverlay(file));
    actions.append(add, over);
    item.append(thumb, name, meta, actions);
    item.addEventListener("dragstart", (event) => {
      event.dataTransfer.setData(BROLL_TYPE, file.name);
      event.dataTransfer.effectAllowed = "copy";
    });
    brollList.append(item);
  }
}

loaded.then(() => {
  pad = padAtLoad || [];
  drawPad();
});
