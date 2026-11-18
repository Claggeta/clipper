// The Sequence lane (ADR 0034, V7 item 9n-3): the finished clip as blocks
// of the source, played in turn. A clip starts with the blocks its span
// and cuts make, drawn faint; the first edit on the lane makes the
// sequence the clip's own: split, duplicate, remove, move and trim, a
// block may repeat. While a clip has its own sequence, cuts are off and
// the handles set only the span the label keeps. A block may be a B-roll
// insert (B-roll amendment, V7 item 9m-2a), played in a second player
// while the source player waits. A block may have zooms, hand crops and
// sound effects of its own (V7 item 9n-5), added in the Frame and Audio
// tabs with "This block only" and marked inside it here. Loaded after
// app.js and precision.js, before preview.js.
"use strict";

const sequenceBox = document.getElementById("sequence");
const sequenceLane = document.getElementById("sequence-lane");
const sequenceState = document.getElementById("sequence-state");
const seqPlayhead = document.getElementById("seq-playhead");
// key -> {own, blocks}: edited and not yet saved. `own` false is the span
// less its cuts, even over a saved sequence (Back to span).
const sequenceEdits = new Map();
// The selected block, and the block playing, by index.
let selectedBlock = 0;
let playingBlock = 0;
// A press on a block is a drag past this many pixels, else a click.
const BLOCK_DRAG_PX = 4;
// B-roll inserts (ADR 0034, B-roll amendment): a hidden player for video,
// an image for a still, and the insert shown, playing or cued.
const brollPlayer = document.getElementById("broll-player");
const brollStill = new Image();
const STILL_SUFFIXES = [".png", ".jpg", ".jpeg"];
const MAX_STILL = 30;
let brollFiles = []; // [{name, folder, still, duration, sound}] from /api/broll
let libraryFolders = []; // the library folders under data/broll (ADR 0035)
// {index, still, from, playing, startedAt, timer}: the insert on show.
let insert = null;
let lastWatch = 0; // when the frame loop last stepped the blocks
// While an edge is dragged, the lane is not rebuilt under the pointer.
let laneDragging = false;
// The block the Frame and Audio tabs last drew for, to redraw them when
// another is selected.
let editorsBlock = -1;

function copyBlock(block) {
  return {
    start: block.start,
    end: block.end,
    zooms: (block.zooms || []).map((zoom) => ({ ...zoom })),
    hand_crops: (block.hand_crops || []).map((hand) => ({ ...hand })),
    effects: (block.effects || []).map((effect) => ({ ...effect })),
    broll: block.broll || null,
    sound: block.sound !== false,
  };
}

function isStill(name) {
  return STILL_SUFFIXES.some((suffix) => name.toLowerCase().endsWith(suffix));
}

function brollFile(name) {
  return brollFiles.find((file) => file.name === name) || null;
}

// How far into its file a B-roll block may run: the video's length, or
// MAX_STILL for a still (unknown length: no limit here; Save checks it).
function brollLimit(name) {
  if (isStill(name)) {
    return MAX_STILL;
  }
  const file = brollFile(name);
  return file && file.duration ? file.duration : Infinity;
}

// The sequence on disk: the label's, else the one the file was rendered with.
function savedSequence(clip) {
  const judged = clip.judgment && clip.judgment.start !== undefined;
  return (judged ? clip.judgment.sequence : clip.sequence) || [];
}

function sequenceEdit(clip) {
  if (!sequenceEdits.has(clip.key)) {
    const saved = savedSequence(clip);
    sequenceEdits.set(clip.key, { own: saved.length > 0, blocks: saved.map(copyBlock) });
  }
  return sequenceEdits.get(clip.key);
}

function ownSequence(clip) {
  return Boolean(clip) && sequenceEdit(clip).own;
}

// The span less its cuts, as blocks in order.
function spanBlocks() {
  const blocks = [];
  let at = span.start;
  for (const [start, end] of activeCuts()) {
    blocks.push(copyBlock({ start: at, end: start }));
    at = end;
  }
  blocks.push(copyBlock({ start: at, end: span.end }));
  return blocks;
}

function blocksOf(clip) {
  return ownSequence(clip) ? sequenceEdit(clip).blocks : spanBlocks();
}

// The first edit on the lane: the blocks shown become the clip's own.
function makeOwn(clip) {
  const edit = sequenceEdit(clip);
  if (!edit.own) {
    edit.blocks = spanBlocks();
    edit.own = true;
    cutIn = null;
  }
  return edit.blocks;
}

function blockLength(block) {
  return block.end - block.start;
}

function sequenceLength() {
  return blocksOf(current).reduce((sum, block) => sum + blockLength(block), 0);
}

// Where each block starts in the finished clip.
function blockStarts(blocks) {
  let at = 0;
  return blocks.map((block) => {
    const start = at;
    at += blockLength(block);
    return start;
  });
}

// Seconds into the finished clip at source time `seconds`: in the block
// playing if it shows that second, else the first that does; NaN when no
// block does.
function sequenceClipTime(seconds) {
  const blocks = blocksOf(current);
  const starts = blockStarts(blocks);
  if (insert && blocks[insert.index] && blocks[insert.index].broll) {
    const block = blocks[insert.index];
    return starts[insert.index] + clamp(insertTime() - block.start, 0, blockLength(block));
  }
  const inside = (i) =>
    !blocks[i].broll && seconds >= blocks[i].start - 0.02 && seconds <= blocks[i].end + 0.02;
  if (!(playingBlock < blocks.length && inside(playingBlock))) {
    const found = blocks.findIndex((_, i) => inside(i));
    if (found < 0) {
      return NaN;
    }
    playingBlock = found;
  }
  const block = blocks[playingBlock];
  return starts[playingBlock] + clamp(seconds - block.start, 0, blockLength(block));
}

// --- Drawing ---

function drawSequence() {
  if (!current || !span) {
    return;
  }
  const own = ownSequence(current);
  const blocks = blocksOf(current);
  selectedBlock = clamp(selectedBlock, 0, blocks.length - 1);
  const total = blocks.reduce((sum, block) => sum + blockLength(block), 0);
  sequenceBox.classList.toggle("own", own);
  sequenceState.textContent = own
    ? `${blocks.length} blocks · ${total.toFixed(1)} s · its own sequence; the handles set only the span`
    : `the span less its cuts · ${total.toFixed(1)} s · edit a block to change the order`;
  // The state is the Block menu's first line and the lane's tooltip.
  sequenceLane.title = sequenceState.textContent;
  document.getElementById("seq-back").disabled = !own;
  for (const element of [...sequenceLane.querySelectorAll(".block")]) {
    element.remove();
  }
  blocks.forEach((block, index) => {
    const element = document.createElement("div");
    element.className = "block";
    element.classList.toggle("selected", index === selectedBlock);
    element.classList.toggle(
      "repeat",
      blocks.some((other, i) => i < index && other.start < block.end && block.start < other.end),
    );
    element.classList.toggle("broll", Boolean(block.broll));
    element.style.flexGrow = String(blockLength(block));
    element.title = blockTitle(block);
    const label = document.createElement("span");
    label.textContent = blockLabel(block, index);
    const startGrip = document.createElement("div");
    startGrip.className = "grip start";
    const endGrip = document.createElement("div");
    endGrip.className = "grip end";
    element.append(startGrip, label, endGrip);
    dragBlock(element, index);
    trimBlock(startGrip, index, "start");
    trimBlock(endGrip, index, "end");
    if (block.broll && isStill(block.broll)) {
      startGrip.hidden = true;
    }
    drawBlockMarks(element, block, index);
    sequenceLane.append(element);
  });
  const chosen = blocks[selectedBlock];
  const file = chosen && chosen.broll ? brollFile(chosen.broll) : null;
  const sound = document.getElementById("seq-sound");
  sound.disabled = !file || file.still || !file.sound;
  sound.setAttribute("aria-pressed", String(Boolean(chosen && chosen.broll && chosen.sound && file && file.sound)));
  sound.textContent = chosen && chosen.broll && chosen.sound && file && file.sound ? "Sound on" : "Sound off";
  drawSequencePlayhead();
  // The overlays are in clip time, so they follow the blocks' length.
  if (typeof drawOverlayLane === "function") {
    drawOverlayLane();
  }
  // "This block only" edits the selected block's: redraw on a new one.
  if (selectedBlock !== editorsBlock && typeof drawAudioEditor === "function") {
    editorsBlock = selectedBlock;
    drawFrameEditor(current);
    drawAudioEditor(current);
  }
}

// A block's own zooms and hand crops as bars along its foot, ends
// dragged in the block's seconds; its own effects as ticks (ADR 0034).
function drawBlockMarks(element, block, index) {
  const length = blockLength(block);
  const items = [
    ...block.zooms.map((item) => ["zoom", item, block.zooms]),
    ...block.hand_crops.map((item) => ["hand", item, block.hand_crops]),
  ];
  for (const [kind, item, list] of items) {
    const mark = document.createElement("div");
    mark.className = `block-mark ${kind}`;
    const place = () => {
      mark.style.left = `${(item.start / length) * 100}%`;
      mark.style.width = `${((item.end - item.start) / length) * 100}%`;
      mark.title = `${kind === "zoom" ? `Zoom ${item.scale}×` : "Hand crop"}, this block only: ${item.start.toFixed(1)} - ${item.end.toFixed(1)} s into it`;
    };
    place();
    mark.append(markGrip(element, mark, item, list, "start", place), markGrip(element, mark, item, list, "end", place));
    mark.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || event.target !== mark) {
        return;
      }
      event.stopPropagation();
      event.preventDefault();
      selectBlock(index, block.start + item.start);
    });
    element.append(mark);
  }
  for (const effect of block.effects) {
    const tick = document.createElement("div");
    tick.className = "block-tick";
    tick.style.left = `${(effect.at_seconds / length) * 100}%`;
    tick.title = `${effect.file}, this block only: ${effect.at_seconds.toFixed(1)} s into it`;
    tick.addEventListener("pointerdown", (event) => {
      event.stopPropagation();
      event.preventDefault();
      selectBlock(index, block.start + effect.at_seconds);
    });
    element.append(tick);
  }
}

// An end of a block's own zoom or crop, dragged inside the block and
// clear of its neighbours in the same list.
function markGrip(element, mark, item, list, edge, place) {
  const handle = document.createElement("i");
  handle.className = `grip ${edge}`;
  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) {
      return;
    }
    event.stopPropagation();
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    const fromX = event.clientX;
    const from = item[edge];
    const length = Number.parseFloat(element.style.flexGrow);
    const perPixel = length / element.getBoundingClientRect().width;
    const others = list.filter((other) => other !== item);
    const onMove = (moveEvent) => {
      laneDragging = true;
      const moved = from + (moveEvent.clientX - fromX) * perPixel;
      if (edge === "start") {
        const floor = Math.max(0, ...others.filter((o) => o.end <= item.start).map((o) => o.end));
        item.start = round(clamp(moved, floor, item.end - MIN_RANGE));
      } else {
        const ceiling = Math.min(length, ...others.filter((o) => o.start >= item.end).map((o) => o.start));
        item.end = round(clamp(moved, item.start + MIN_RANGE, ceiling));
      }
      place();
      drawUnsaved();
    };
    const onUp = () => {
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
      handle.removeEventListener("pointercancel", onUp);
      laneDragging = false;
      blockEditsChanged();
    };
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
    handle.addEventListener("pointercancel", onUp);
  });
  return handle;
}

// After a block's own edit: the lane, the tabs and the unsaved mark.
function blockEditsChanged() {
  drawUnsaved();
  drawSequence();
  drawFrameEditor(current);
  drawAudioEditor(current);
}

// The selected block, when the "This block only" box is ticked and the
// block can take its own edits: any block for effects (`brollOk`), a
// block of the source for zooms and crops. With `make`, the sequence
// becomes the clip's own first, as any edit on the lane does.
function blockTarget(box, brollOk, make = false) {
  const ticked = box instanceof HTMLInputElement ? box.checked && !box.disabled : Boolean(box);
  if (!current || !span || !ticked) {
    return null;
  }
  const blocks = make ? makeOwn(current) : blocksOf(current);
  const block = blocks[selectedBlock];
  if (!block || (block.broll && !brollOk)) {
    return null;
  }
  return block;
}

// Seconds into block `index` at the playhead: the source's or the insert's
// time when that block is on show, else its start.
function timeInBlock(index) {
  const block = blocksOf(current)[index];
  if (block.broll) {
    return insert && insert.index === index ? insertTime() - block.start : 0;
  }
  const seconds = player.currentTime;
  const showing = !insert && playingBlock === index && seconds >= block.start && seconds <= block.end;
  return showing ? seconds - block.start : 0;
}

// The block of the source playing at `seconds` in a clip with its own
// sequence, for its own zooms and crops; null otherwise.
function playingSourceBlock(seconds) {
  if (!current || !span || insert || !ownSequence(current)) {
    return null;
  }
  if (!Number.isFinite(sequenceClipTime(seconds))) {
    return null;
  }
  const block = blocksOf(current)[playingBlock];
  return block && !block.broll ? block : null;
}

function blockTitle(block) {
  const length = `${blockLength(block).toFixed(1)} s`;
  if (block.broll) {
    const sound = block.sound ? "" : ", silent";
    return `${block.broll}: ${fmtTenths(block.start)} - ${fmtTenths(block.end)} (${length}${sound})`;
  }
  return `${fmtTenths(block.start)} - ${fmtTenths(block.end)} (${length})`;
}

function blockLabel(block, index) {
  return block.broll ? `${index + 1} · ▣ ${block.broll}` : `${index + 1} · ${fmt(block.start)}`;
}

function drawSequencePlayhead() {
  if (!current || !span) {
    return;
  }
  const total = sequenceLength();
  const at = total ? sequenceClipTime(player.currentTime) : NaN;
  seqPlayhead.hidden = !Number.isFinite(at);
  if (Number.isFinite(at)) {
    seqPlayhead.style.left = `${(at / total) * 100}%`;
  }
}

// Called by drawMarks: the lane follows the span and cuts, except while
// one of its own edges is being dragged.
function drawSequenceSoft() {
  if (!laneDragging) {
    drawSequence();
  }
}

// After an edit: redraw the lane, the strip's marks and the times.
function sequenceChanged() {
  notice("");
  drawSequence();
  drawMarks();
}

// --- Editing ---

function selectBlock(index, seconds) {
  selectedBlock = index;
  playingBlock = index;
  if (blocksOf(current)[index].broll) {
    player.pause();
    startInsert(index, seconds, false);
  } else {
    stopInsert();
    player.currentTime = seconds;
  }
  drawSequence();
}

// A click selects the block and seeks to where it was pressed; a drag
// moves it to where it is dropped.
function dragBlock(element, index) {
  element.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.classList.contains("grip")) {
      return;
    }
    event.preventDefault();
    const fromX = event.clientX;
    let dragging = false;
    element.setPointerCapture(event.pointerId);
    const onMove = (moveEvent) => {
      if (!dragging && Math.abs(moveEvent.clientX - fromX) < BLOCK_DRAG_PX) {
        return;
      }
      dragging = true;
      element.classList.add("dragging");
    };
    const onUp = (upEvent) => {
      element.removeEventListener("pointermove", onMove);
      element.removeEventListener("pointerup", onUp);
      element.removeEventListener("pointercancel", onUp);
      element.classList.remove("dragging");
      const blocks = blocksOf(current);
      if (!dragging) {
        const box = element.getBoundingClientRect();
        const fraction = clamp((upEvent.clientX - box.left) / box.width, 0, 1);
        const block = blocks[index];
        selectBlock(index, block.start + fraction * blockLength(block));
        return;
      }
      if (upEvent.type !== "pointerup") {
        return;
      }
      const target = dropIndex(upEvent.clientX);
      moveBlockTo(index, target);
    };
    element.addEventListener("pointermove", onMove);
    element.addEventListener("pointerup", onUp);
    element.addEventListener("pointercancel", onUp);
  });
}

// The index a block dropped at `clientX` goes to: before the first block
// whose middle is right of the pointer.
function dropIndex(clientX) {
  const elements = [...sequenceLane.querySelectorAll(".block")];
  const after = elements.findIndex((element) => {
    const box = element.getBoundingClientRect();
    return clientX < box.left + box.width / 2;
  });
  return after < 0 ? elements.length : after;
}

function moveBlockTo(from, target) {
  const blocks = makeOwn(current);
  const [block] = blocks.splice(from, 1);
  const to = target > from ? target - 1 : target;
  blocks.splice(to, 0, block);
  selectedBlock = to;
  playingBlock = to;
  sequenceChanged();
}

function moveBlock(direction) {
  if (!current) {
    return;
  }
  const blocks = blocksOf(current);
  const to = selectedBlock + direction;
  if (blocks.length < 2 || to < 0 || to >= blocks.length) {
    return;
  }
  moveBlockTo(selectedBlock, direction > 0 ? to + 1 : to);
}

// A drag on a block's edge moves its start or end in the source: at least
// MIN_SPAN long, inside the video, snapped to a word edge (Alt frees it).
function trimBlock(grip, index, edge) {
  grip.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    selectedBlock = index;
    const fromX = event.clientX;
    const from = blocksOf(current)[index][edge];
    const fromStart = blocksOf(current)[index].start;
    const perPixel = sequenceLength() / sequenceLane.getBoundingClientRect().width;
    grip.setPointerCapture(event.pointerId);
    const onMove = (moveEvent) => {
      // The first move makes the sequence the clip's own; a click does not.
      const block = makeOwn(current)[index];
      const moved = from + (moveEvent.clientX - fromX) * perPixel;
      if (block.broll) {
        // In the B-roll's own file: from 0 to its length, unsnapped.
        if (edge === "start") {
          block.start = round(clamp(moved, 0, block.end - MIN_SPAN));
        } else {
          block.end = round(clamp(moved, block.start + MIN_SPAN, brollLimit(block.broll)));
        }
        startInsert(index, block[edge], false);
      } else if (edge === "start") {
        block.start = round(clamp(snap(moved, edge, moveEvent.altKey), 0, block.end - MIN_SPAN));
        player.currentTime = block.start;
      } else {
        block.end = round(clamp(snap(moved, edge, moveEvent.altKey), block.start + MIN_SPAN, duration));
        player.currentTime = block.end;
      }
      playingBlock = index;
      laneDragging = true;
      const element = grip.parentElement;
      element.style.flexGrow = String(blockLength(block));
      element.title = blockTitle(block);
      element.querySelector("span").textContent = blockLabel(block, index);
      drawMarks();
    };
    const onUp = () => {
      grip.removeEventListener("pointermove", onMove);
      grip.removeEventListener("pointerup", onUp);
      grip.removeEventListener("pointercancel", onUp);
      laneDragging = false;
      const dropped = ownSequence(current) ? fitOwnEdits(blocksOf(current)[index], fromStart) : 0;
      sequenceChanged();
      if (dropped) {
        sequenceNotice(`Dropped ${dropped} of the block's own edit${dropped > 1 ? "s" : ""} that no longer fit${dropped > 1 ? "" : "s"} in it.`);
      }
      drawFrameEditor(current);
      drawAudioEditor(current);
    };
    grip.addEventListener("pointermove", onMove);
    grip.addEventListener("pointerup", onUp);
    grip.addEventListener("pointercancel", onUp);
  });
}

function sequenceNotice(message) {
  notice(message);
}

// After a trim: a block's own edits stay on the same footage (its start
// moved from `fromStart`), and those no longer inside it are dropped.
// Returns how many were dropped.
function fitOwnEdits(block, fromStart) {
  const shift = block.start - fromStart;
  const length = blockLength(block);
  let dropped = 0;
  const keep = (items, inside) => {
    const kept = items.filter(inside);
    dropped += items.length - kept.length;
    return kept;
  };
  const moved = (items) =>
    items.map((item) => ({ ...item, start: round(item.start - shift), end: round(item.end - shift) }));
  if (!block.broll) {
    block.zooms = keep(moved(block.zooms), (z) => z.start >= 0 && z.end <= length + 0.001);
    block.hand_crops = keep(moved(block.hand_crops), (h) => h.start >= 0 && h.end <= length + 0.001);
  }
  block.effects = keep(
    block.effects.map((e) => ({ ...e, at_seconds: round(e.at_seconds - shift) })),
    (e) => e.at_seconds >= 0 && e.at_seconds < length,
  );
  return dropped;
}

// Split the block at the playhead in two; the second half is selected.
function splitBlock() {
  if (!current) {
    return;
  }
  const blocks = blocksOf(current);
  if (insert && blocks[insert.index] && blocks[insert.index].broll) {
    splitAt(insert.index, round(insertTime()));
    return;
  }
  const seconds = round(player.currentTime);
  // The block playing if the playhead is in it, else the first that shows it.
  const inside = (block) =>
    block && !block.broll && seconds > block.start && seconds < block.end;
  const found = inside(blocks[playingBlock])
    ? playingBlock
    : blocks.findIndex(inside);
  if (found < 0) {
    sequenceNotice("Put the playhead inside a block to split it.");
    return;
  }
  splitAt(found, seconds);
}

// Split block `found` at `seconds`, in its own time (the source's, or a
// B-roll file's).
function splitAt(found, seconds) {
  const block = blocksOf(current)[found];
  if (block.broll && isStill(block.broll)) {
    sequenceNotice("A still is not split; trim its end instead.");
    return;
  }
  if (seconds - block.start < MIN_SPAN || block.end - seconds < MIN_SPAN) {
    sequenceNotice(`Each half must be at least ${MIN_SPAN} s.`);
    return;
  }
  const own = makeOwn(current);
  const first = copyBlock(own[found]);
  first.end = seconds;
  const second = copyBlock(own[found]);
  second.start = seconds;
  // A block's own edits are in seconds from its start (ADR 0034).
  const at = seconds - block.start;
  first.zooms = first.zooms.filter((z) => z.end <= at);
  first.hand_crops = first.hand_crops.filter((h) => h.end <= at);
  first.effects = first.effects.filter((e) => e.at_seconds < at);
  second.zooms = second.zooms
    .filter((z) => z.start >= at)
    .map((z) => ({ ...z, start: z.start - at, end: z.end - at }));
  second.hand_crops = second.hand_crops
    .filter((h) => h.start >= at)
    .map((h) => ({ ...h, start: h.start - at, end: h.end - at }));
  second.effects = second.effects
    .filter((e) => e.at_seconds >= at)
    .map((e) => ({ ...e, at_seconds: e.at_seconds - at }));
  own.splice(found, 1, first, second);
  selectedBlock = found + 1;
  playingBlock = found + 1;
  sequenceNotice("");
  sequenceChanged();
}

function duplicateBlock() {
  if (!current) {
    return;
  }
  const blocks = makeOwn(current);
  blocks.splice(selectedBlock + 1, 0, copyBlock(blocks[selectedBlock]));
  selectedBlock += 1;
  sequenceNotice("");
  sequenceChanged();
}

function removeBlock() {
  if (!current) {
    return;
  }
  if (blocksOf(current).length < 2) {
    sequenceNotice("A clip needs at least one block.");
    return;
  }
  const blocks = makeOwn(current);
  blocks.splice(selectedBlock, 1);
  selectedBlock = Math.min(selectedBlock, blocks.length - 1);
  sequenceNotice("");
  sequenceChanged();
}

// Drop the clip's own sequence: back to the span less its cuts.
function backToSpan() {
  if (!current) {
    return;
  }
  stopInsert();
  sequenceEdits.set(current.key, { own: false, blocks: [] });
  selectedBlock = 0;
  sequenceNotice("");
  sequenceChanged();
}

document.getElementById("seq-split").addEventListener("click", splitBlock);
document.getElementById("seq-duplicate").addEventListener("click", duplicateBlock);
document.getElementById("seq-remove").addEventListener("click", removeBlock);
document.getElementById("seq-left").addEventListener("click", () => moveBlock(-1));
document.getElementById("seq-right").addEventListener("click", () => moveBlock(1));
document.getElementById("seq-back").addEventListener("click", backToSpan);

// Sound on or off for the selected insert, when its file has any.
document.getElementById("seq-sound").addEventListener("click", () => {
  const blocks = makeOwn(current);
  const block = blocks[selectedBlock];
  if (!block || !block.broll) {
    return;
  }
  block.sound = !block.sound;
  if (insert && insert.index === selectedBlock) {
    brollPlayer.muted = !block.sound;
  }
  sequenceChanged();
});

// Put a B-roll file in the clip's sequence at `index`: the first 3 s of a
// video (all of it if shorter), or 2 s of a still.
function insertBroll(file, index) {
  if (!current) {
    return;
  }
  // A library clip (ADR 0035) goes in whole; other B-roll for 3 s.
  const whole = file.folder && !file.still && file.duration ? Math.floor(file.duration * 100) / 100 : 0;
  const length = file.still ? 2 : whole || Math.min(3, file.duration || 3);
  const blocks = makeOwn(current);
  const at = clamp(index, 0, blocks.length);
  blocks.splice(at, 0, copyBlock({ start: 0, end: length, broll: file.name, sound: file.sound }));
  selectedBlock = at;
  playingBlock = at;
  sequenceChanged();
}

// --- The insert on show: a B-roll block played or cued ---

// Seconds into the insert's own file.
function insertTime() {
  if (!insert) {
    return 0;
  }
  if (insert.still) {
    return insert.playing ? insert.from + (performance.now() - insert.startedAt) / 1000 : insert.from;
  }
  return brollPlayer.currentTime;
}

function insertPlaying() {
  return Boolean(insert && insert.playing);
}

// Show block `index`, a B-roll insert, from `from` seconds into its file,
// playing or cued; the source player waits.
function startInsert(index, from, playing) {
  const block = blocksOf(current)[index];
  const src = `/broll/${encodeURIComponent(block.broll)}`;
  const still = isStill(block.broll);
  clearTimeout(insert && insert.timer);
  insert = { index, still, from, playing, startedAt: performance.now(), timer: 0 };
  if (still) {
    if (brollStill.getAttribute("src") !== src) {
      brollStill.src = src;
    }
    brollPlayer.pause();
    if (playing) {
      // A still has no player to keep frames coming, so its end is also
      // timed, in case the page draws no frames meanwhile.
      const left = (block.end - from) * 1000 + 30;
      insert.timer = setTimeout(stillEnded, left);
    }
    return;
  }
  if (brollPlayer.getAttribute("src") !== src) {
    brollPlayer.src = src;
  }
  brollPlayer.muted = !block.sound;
  brollPlayer.currentTime = from;
  if (playing) {
    brollPlayer.play().catch(() => {
      if (insert) {
        insert.playing = false;
      }
    });
  } else {
    brollPlayer.pause();
  }
}

function stopInsert() {
  if (insert) {
    clearTimeout(insert.timer);
    brollPlayer.pause();
    insert = null;
  }
}

// What the preview draws during an insert: the video or the still, once
// it has a frame; null otherwise.
function insertFrame() {
  if (!insert) {
    return null;
  }
  if (insert.still) {
    return brollStill.complete && brollStill.naturalWidth
      ? { media: brollStill, width: brollStill.naturalWidth, height: brollStill.naturalHeight }
      : null;
  }
  return brollPlayer.videoWidth
    ? { media: brollPlayer, width: brollPlayer.videoWidth, height: brollPlayer.videoHeight }
    : null;
}

async function loadBroll() {
  try {
    const response = await fetch("/api/broll");
    const data = await response.json();
    brollFiles = data.broll || [];
    libraryFolders = data.folders || [];
  } catch {
    brollFiles = [];
    libraryFolders = [];
  }
  if (typeof drawBrollFiles === "function") {
    drawBrollFiles();
  }
}

// --- Playing through the blocks ---

// Play the blocks in turn from block `index` (or the one at the playhead).
function playSequence(index = null) {
  const blocks = blocksOf(current);
  let from = index;
  let at = null;
  if (from === null) {
    const seconds = player.currentTime;
    const playing = blocks[playingBlock];
    if (playing && seconds >= playing.start && seconds < playing.end - 0.05) {
      from = playingBlock;
      at = seconds;
    } else {
      from = 0;
    }
  }
  playingBlock = from;
  if (blocks[from].broll) {
    preview = { end: Infinity, settling: false, sequence: true };
    startInsert(from, blocks[from].start, true);
    requestAnimationFrame(watchPreview);
    return;
  }
  stopInsert();
  const start = at === null ? blocks[from].start : at;
  // Setting the time it already has still fires `seeking`, which would end
  // the preview, so the time is set only when it changes.
  const seeks = Math.abs(player.currentTime - start) > 0.01;
  preview = { end: Infinity, settling: seeks, sequence: true };
  if (seeks) {
    player.currentTime = start;
  }
  player.play().catch(() => {
    preview = null;
  });
  requestAnimationFrame(watchPreview);
}

// Pause the blocks while an insert plays.
function pauseInsert() {
  if (insert) {
    insert.from = insertTime();
    insert.playing = false;
    clearTimeout(insert.timer);
    brollPlayer.pause();
  }
  preview = null;
}

// Called by watchPreview each frame while the blocks play: on to the next
// block at a block's end, a seek of our own; paused after the last.
function watchSequence() {
  lastWatch = performance.now();
  if (stepSequence()) {
    requestAnimationFrame(watchPreview);
  }
}

// When the still's timer fires: step, and start the frame loop again if
// it stalled while nothing played.
function stillEnded() {
  if (stepSequence() && performance.now() - lastWatch > 200) {
    requestAnimationFrame(watchPreview);
  }
}

// One step of playing the blocks: on to the next block when this one has
// ended. False once the blocks are done or stopped.
function stepSequence() {
  if (!preview || !preview.sequence) {
    return false;
  }
  const blocks = blocksOf(current);
  const block = blocks[playingBlock];
  if (!block) {
    stopInsert();
    player.pause();
    return false;
  }
  const ended = block.broll
    ? insertPlaying() && insertTime() >= block.end - 0.02
    : !preview.settling && player.currentTime >= block.end;
  if (ended) {
    const next = playingBlock + 1;
    if (next >= blocks.length) {
      stopInsert();
      preview = null;
      player.pause();
      return false;
    }
    playingBlock = next;
    const after = blocks[next];
    if (after.broll) {
      // The source waits; its pause is ours, not the user's.
      preview.handoff = !player.paused;
      player.pause();
      startInsert(next, after.start, true);
    } else {
      stopInsert();
      preview.settling = true;
      player.currentTime = after.start;
      player.play().catch(() => {});
    }
  }
  return true;
}

// --- What Save sends ---

// A sequence that is just the span less some cuts: in source order, apart,
// from the start handle to the end handle, with no edits of its own. Its
// cuts, or null for any other sequence (ADR 0034, decision 1).
function sequenceAsCuts(blocks) {
  if (!sameTime(blocks[0].start, span.start) || !sameTime(blocks[blocks.length - 1].end, span.end)) {
    return null;
  }
  const cuts = [];
  for (let i = 0; i < blocks.length; i += 1) {
    const block = blocks[i];
    if (block.broll || block.zooms.length || block.hand_crops.length || block.effects.length) {
      return null;
    }
    if (i > 0) {
      const gap = [blocks[i - 1].end, block.start];
      if (gap[1] < gap[0] + MIN_CUT) {
        if (!sameTime(gap[0], gap[1])) {
          return null;
        }
        continue;
      }
      cuts.push(gap);
    }
  }
  return cuts;
}

// The cuts and blocks Save sends: the span's cuts, or the sequence's when
// it is only cuts, or the sequence with no cuts.
function sentShape(clip) {
  if (!ownSequence(clip)) {
    return { cuts: activeCuts(), sequence: [] };
  }
  const blocks = sequenceEdit(clip).blocks;
  const cuts = sequenceAsCuts(blocks);
  if (cuts !== null) {
    return { cuts, sequence: [] };
  }
  return { cuts: [], sequence: blocks.map(copyBlock) };
}

function sameSequence(a, b) {
  return (
    a.length === b.length &&
    a.every(
      (block, i) =>
        sameTime(block.start, b[i].start) &&
        sameTime(block.end, b[i].end) &&
        (block.broll || null) === (b[i].broll || null) &&
        (block.sound !== false) === (b[i].sound !== false) &&
        ownEditsText(block) === ownEditsText(b[i]),
    )
  );
}

// A block's own edits as text, to the millisecond, for comparing.
function ownEditsText(block) {
  const ms = (value) => Math.round(value * 1000);
  return JSON.stringify([
    (block.zooms || []).map((z) => [ms(z.start), ms(z.end), z.scale, ms(z.x), ms(z.y), Boolean(z.smooth)]),
    (block.hand_crops || []).map((h) => [ms(h.start), ms(h.end), ms(h.x)]),
    (block.effects || []).map((e) => [e.file, ms(e.at_seconds), e.level_db]),
  ]);
}

// A Save went through, or the clip was reset: back to what is on disk.
function resetSequence(clip, toSpan = false) {
  if (clip === current) {
    stopInsert();
  }
  if (toSpan) {
    sequenceEdits.set(clip.key, { own: false, blocks: [] });
  } else {
    sequenceEdits.delete(clip.key);
  }
  selectedBlock = 0;
  playingBlock = 0;
  editorsBlock = -1;
  if (clip === current) {
    drawSequence();
  }
}

player.addEventListener("timeupdate", drawSequencePlayhead);
brollPlayer.addEventListener("timeupdate", drawSequencePlayhead);
loaded.then(loadBroll);
