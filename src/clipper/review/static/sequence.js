// The Sequence lane (ADR 0034, V7 item 9n-3): the finished clip as blocks
// of the source, played in turn. A clip starts with the blocks its span
// and cuts make, drawn faint; the first edit on the lane makes the
// sequence the clip's own: split, duplicate, remove, move and trim, a
// block may repeat. While a clip has its own sequence, cuts are off and
// the handles set only the span the label keeps. Loaded after app.js and
// precision.js, before preview.js.
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
// While an edge is dragged, the lane is not rebuilt under the pointer.
let laneDragging = false;

function copyBlock(block) {
  return {
    start: block.start,
    end: block.end,
    zooms: (block.zooms || []).map((zoom) => ({ ...zoom })),
    hand_crops: (block.hand_crops || []).map((hand) => ({ ...hand })),
    effects: (block.effects || []).map((effect) => ({ ...effect })),
  };
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
  const inside = (i) => seconds >= blocks[i].start - 0.02 && seconds <= blocks[i].end + 0.02;
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
    element.style.flexGrow = String(blockLength(block));
    element.title = `${fmtTenths(block.start)} - ${fmtTenths(block.end)} (${blockLength(block).toFixed(1)} s)`;
    const label = document.createElement("span");
    label.textContent = `${index + 1} · ${fmt(block.start)}`;
    const startGrip = document.createElement("div");
    startGrip.className = "grip start";
    const endGrip = document.createElement("div");
    endGrip.className = "grip end";
    element.append(startGrip, label, endGrip);
    dragBlock(element, index);
    trimBlock(startGrip, index, "start");
    trimBlock(endGrip, index, "end");
    sequenceLane.append(element);
  });
  drawSequencePlayhead();
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
  player.currentTime = seconds;
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
    const perPixel = sequenceLength() / sequenceLane.getBoundingClientRect().width;
    grip.setPointerCapture(event.pointerId);
    const onMove = (moveEvent) => {
      // The first move makes the sequence the clip's own; a click does not.
      const block = makeOwn(current)[index];
      const seconds = snap(
        from + (moveEvent.clientX - fromX) * perPixel,
        edge,
        moveEvent.altKey,
      );
      if (edge === "start") {
        block.start = round(clamp(seconds, 0, block.end - MIN_SPAN));
        player.currentTime = block.start;
      } else {
        block.end = round(clamp(seconds, block.start + MIN_SPAN, duration));
        player.currentTime = block.end;
      }
      playingBlock = index;
      laneDragging = true;
      const element = grip.parentElement;
      element.style.flexGrow = String(blockLength(block));
      element.title = `${fmtTenths(block.start)} - ${fmtTenths(block.end)} (${blockLength(block).toFixed(1)} s)`;
      element.querySelector("span").textContent = `${index + 1} · ${fmt(block.start)}`;
      drawMarks();
    };
    const onUp = () => {
      grip.removeEventListener("pointermove", onMove);
      grip.removeEventListener("pointerup", onUp);
      grip.removeEventListener("pointercancel", onUp);
      laneDragging = false;
      sequenceChanged();
    };
    grip.addEventListener("pointermove", onMove);
    grip.addEventListener("pointerup", onUp);
    grip.addEventListener("pointercancel", onUp);
  });
}

function sequenceNotice(message) {
  notice(message);
}

// Split the block at the playhead in two; the second half is selected.
function splitBlock() {
  if (!current) {
    return;
  }
  const seconds = round(player.currentTime);
  const blocks = blocksOf(current);
  // The block playing if the playhead is in it, else the first that shows it.
  const inside = (block) => block && seconds > block.start && seconds < block.end;
  const found = inside(blocks[playingBlock])
    ? playingBlock
    : blocks.findIndex(inside);
  if (found < 0) {
    sequenceNotice("Put the playhead inside a block to split it.");
    return;
  }
  const block = blocks[found];
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
  const start = at === null ? blocks[from].start : at;
  const seeks = Math.abs(player.currentTime - start) > 0.01;
  preview = { end: Infinity, settling: seeks, sequence: true };
  player.currentTime = start;
  player.play().catch(() => {
    preview = null;
  });
  requestAnimationFrame(watchPreview);
}

// Called by watchPreview each frame while the blocks play: on to the next
// block at a block's end, a seek of our own; paused after the last.
function watchSequence() {
  const blocks = blocksOf(current);
  const block = blocks[playingBlock];
  if (!block) {
    player.pause();
    return;
  }
  if (!preview.settling && player.currentTime >= block.end) {
    const next = playingBlock + 1;
    if (next >= blocks.length) {
      player.pause();
      return;
    }
    playingBlock = next;
    preview.settling = true;
    player.currentTime = blocks[next].start;
  }
  requestAnimationFrame(watchPreview);
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
    if (block.zooms.length || block.hand_crops.length || block.effects.length) {
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
        JSON.stringify([block.zooms || [], block.hand_crops || [], block.effects || []]) ===
          JSON.stringify([b[i].zooms || [], b[i].hand_crops || [], b[i].effects || []]),
    )
  );
}

// A Save went through, or the clip was reset: back to what is on disk.
function resetSequence(clip, toSpan = false) {
  if (toSpan) {
    sequenceEdits.set(clip.key, { own: false, blocks: [] });
  } else {
    sequenceEdits.delete(clip.key);
  }
  selectedBlock = 0;
  playingBlock = 0;
  if (clip === current) {
    drawSequence();
  }
}

player.addEventListener("timeupdate", drawSequencePlayhead);
