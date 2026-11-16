// Zooms and hand crops (ADR 0016 and 0025 amendments; V7 item 9h): rows in
// the Frame tab, bars on the timeline whose ends drag, and (in preview.js)
// drawing and panning on the preview. Times are source seconds, shown as
// seconds into the span; with "This block only" (ADR 0034, V7 item 9n-5)
// the selected block's own, in seconds from its start, kept on the block.
// Kept per clip until Save, which re-renders.
// Loaded after app.js and before preview.js.
"use strict";

const zoomsBox = document.getElementById("zooms");
const handsBox = document.getElementById("hands");
const frameNote = document.getElementById("frame-note");
const frameLane = document.getElementById("frame-lane");
const frameBlockOnly = document.getElementById("frame-block-only");
// key -> {zooms: [{start, end, scale, x, y, smooth}], hands: [{start, end, x}]}
const frameEdits = new Map();
// A new zoom or hand crop lasts this long, in seconds, if the span allows.
const NEW_ZOOM_SECONDS = 2;
const NEW_HAND_SECONDS = 3;
// The renderer's ease for a smooth zoom (rendering/ffmpeg.py).
const ZOOM_EASE_SECONDS = 0.3;
const ZOOM_SCALES = [1.1, 1.2, 1.3, 1.5, 1.75, 2];
const MIN_RANGE = 0.2;

function savedFrame(clip) {
  const judged = clip.judgment && clip.judgment.start !== undefined;
  const source = judged ? clip.judgment : clip;
  return { zooms: source.zooms || [], hands: source.hand_crops || [] };
}

function frameOf(clip) {
  if (!frameEdits.has(clip.key)) {
    const saved = savedFrame(clip);
    frameEdits.set(clip.key, {
      zooms: saved.zooms.map((zoom) => ({ ...zoom })),
      hands: saved.hands.map((hand) => ({ ...hand })),
    });
  }
  return frameEdits.get(clip.key);
}

function sentZooms(clip) {
  return frameOf(clip).zooms.map((z) => ({
    start: round(z.start),
    end: round(z.end),
    scale: z.scale,
    x: Math.round(z.x * 1000) / 1000,
    y: Math.round(z.y * 1000) / 1000,
    smooth: Boolean(z.smooth),
  }));
}

function sentHands(clip) {
  return frameOf(clip).hands.map((h) => ({
    start: round(h.start),
    end: round(h.end),
    x: Math.round(h.x * 1000) / 1000,
  }));
}

function sameFrame(clip) {
  const saved = savedFrame(clip);
  const text = (zooms, hands) =>
    JSON.stringify([
      zooms.map((z) => [round(z.start), round(z.end), z.scale, Math.round(z.x * 1000), Math.round(z.y * 1000), Boolean(z.smooth)]),
      hands.map((h) => [round(h.start), round(h.end), Math.round(h.x * 1000)]),
    ]);
  return text(sentZooms(clip), sentHands(clip)) === text(saved.zooms, saved.hands);
}

function frameChanged() {
  drawUnsaved();
  drawFrameLane();
  // A block's own zooms and crops are marked on the Sequence lane.
  drawSequenceSoft();
}

// The lists the tab edits: the clip's, in source seconds shown from the
// span's start, or with "This block only" the selected block's own, in
// seconds from its start (ADR 0034, block edits amendment).
function frameTarget(clip) {
  const block = blockTarget(frameBlockOnly, false);
  if (block) {
    return { zooms: block.zooms, hands: block.hand_crops, base: 0, low: 0, high: blockLength(block), block };
  }
  const edits = frameOf(clip);
  return { zooms: edits.zooms, hands: edits.hands, base: span.start, low: span.start, high: span.end, block: null };
}

// --- The rows ---

function relativeInput(seconds, onChange, base = span.start) {
  return numberInput(round(seconds - base), (relative) => onChange(base + relative));
}

function removeButton(onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "Remove";
  button.addEventListener("click", onClick);
  return button;
}

function drawFrameEditor(clip) {
  const edits = frameTarget(clip);
  const base = edits.base;
  const into = edits.block ? "s into the block" : "s into the span";
  drawFrameBlockOnly(edits.block);
  zoomsBox.replaceChildren();
  edits.zooms.forEach((zoom, index) => {
    const row = document.createElement("div");
    row.className = "range-row";
    const start = relativeInput(
      zoom.start,
      (seconds) => {
        zoom.start = seconds;
        frameChanged();
      },
      base,
    );
    start.title = `From (${into})`;
    const end = relativeInput(
      zoom.end,
      (seconds) => {
        zoom.end = seconds;
        frameChanged();
      },
      base,
    );
    end.title = `To (${into})`;
    const scale = selectInput(
      ZOOM_SCALES.map((value) => [String(value), `${value}×`]),
      String(zoom.scale),
      (value) => {
        zoom.scale = Number(value);
        frameChanged();
      },
    );
    scale.title = "Zoom";
    const smooth = document.createElement("label");
    smooth.className = "fade";
    const smoothBox = document.createElement("input");
    smoothBox.type = "checkbox";
    smoothBox.checked = Boolean(zoom.smooth);
    smoothBox.addEventListener("change", () => {
      zoom.smooth = smoothBox.checked;
      frameChanged();
    });
    smooth.append(smoothBox, " Smooth");
    row.append(
      start,
      end,
      scale,
      smooth,
      removeButton(() => {
        edits.zooms.splice(index, 1);
        drawFrameEditor(clip);
        frameChanged();
      }),
    );
    zoomsBox.append(row);
  });
  handsBox.replaceChildren();
  edits.hands.forEach((hand, index) => {
    const row = document.createElement("div");
    row.className = "range-row";
    const start = relativeInput(
      hand.start,
      (seconds) => {
        hand.start = seconds;
        frameChanged();
      },
      base,
    );
    start.title = `From (${into})`;
    const end = relativeInput(
      hand.end,
      (seconds) => {
        hand.end = seconds;
        frameChanged();
      },
      base,
    );
    end.title = `To (${into})`;
    const where = document.createElement("span");
    where.className = "muted";
    where.textContent = `centre ${Math.round(hand.x * 100)}%`;
    row.append(
      start,
      end,
      where,
      removeButton(() => {
        edits.hands.splice(index, 1);
        drawFrameEditor(clip);
        frameChanged();
      }),
    );
    handsBox.append(row);
  });
  const vertical = isVertical;
  document.getElementById("add-zoom").disabled = !vertical || edits.zooms.length >= 10;
  document.getElementById("add-hand").disabled = !vertical || edits.hands.length >= 10;
  const others = blocksOf(clip).filter((b) => b.zooms.length || b.hand_crops.length).length;
  frameNote.textContent = !vertical
    ? "Zooms and hand crops need a vertical drafts folder."
    : !edits.block && others
      ? `${others} block${others > 1 ? "s have their" : " has its"} own zooms or crops, which replace these there.`
      : "Drag on the preview to move a zoom's centre or a hand crop's window.";
  drawFrameLane();
}

// The "This block only" box: usable while a block of the source is
// selected; the headings say whose seconds the rows are in.
function drawFrameBlockOnly(block) {
  const chosen = blocksOf(current)[selectedBlock];
  frameBlockOnly.disabled = !chosen || Boolean(chosen.broll);
  document.getElementById("frame-block-name").textContent = chosen
    ? chosen.broll
      ? "(a B-roll insert takes none)"
      : `(block ${selectedBlock + 1})`
    : "";
  const unit = block ? `(block ${selectedBlock + 1} only, seconds into the block)` : "(seconds into the span)";
  document.getElementById("zooms-unit").textContent = unit;
  document.getElementById("hands-unit").textContent = block
    ? unit
    : "(a 9:16 window placed by hand)";
}

// A new stretch at the playhead, in the span (or in the block, for its
// own), apart from the others.
function addRange(kind, length, make) {
  const block = blockTarget(frameBlockOnly, false, true);
  let list;
  let from;
  let to;
  if (block) {
    list = kind === "zoom" ? block.zooms : block.hand_crops;
    const blockEnd = blockLength(block);
    from = clamp(timeInBlock(selectedBlock), 0, blockEnd - MIN_RANGE);
    to = Math.min(from + length, blockEnd);
  } else {
    list = kind === "zoom" ? frameOf(current).zooms : frameOf(current).hands;
    from = clamp(player.currentTime, span.start, span.end - MIN_RANGE);
    to = Math.min(from + length, span.end);
  }
  if (list.some((item) => from < item.end && item.start < to)) {
    frameNote.textContent = "That overlaps another; move the playhead first.";
    return;
  }
  list.push(make(round(from), round(to)));
  list.sort((a, b) => a.start - b.start);
  drawFrameEditor(current);
  frameChanged();
}

document.getElementById("add-zoom").addEventListener("click", () => {
  addRange("zoom", NEW_ZOOM_SECONDS, (start, end) => ({
    start,
    end,
    scale: 1.3,
    x: 0.5,
    y: 0.5,
    smooth: false,
  }));
});

document.getElementById("add-hand").addEventListener("click", () => {
  addRange("hand", NEW_HAND_SECONDS, (start, end) => ({ start, end, x: 0.5 }));
});

frameBlockOnly.addEventListener("change", () => {
  if (current) {
    drawFrameEditor(current);
  }
});

// --- The timeline lane: a bar per zoom and hand crop, ends dragged ---

function drawFrameLane() {
  frameLane.replaceChildren();
  if (!current || !span) {
    return;
  }
  const edits = frameOf(current);
  const bars = [
    ...edits.zooms.map((item) => ["zoom", item, `${item.scale}×`]),
    ...edits.hands.map((item) => ["hand", item, "crop"]),
  ];
  frameLane.hidden = bars.length === 0;
  for (const [kind, item, label] of bars) {
    const bar = document.createElement("div");
    bar.className = `frame-bar ${kind}`;
    bar.style.left = `${percent(item.start)}%`;
    bar.style.width = `${percent(item.end) - percent(item.start)}%`;
    bar.title = `${kind === "zoom" ? "Zoom" : "Hand crop"} ${fmtTenths(item.start)} – ${fmtTenths(item.end)}`;
    const text = document.createElement("span");
    text.textContent = label;
    bar.append(grip(item, "start"), text, grip(item, "end"));
    // A press on the bar goes to its start, not the strip's click or cut.
    bar.addEventListener("pointerdown", (event) => {
      event.stopPropagation();
      player.currentTime = item.start;
    });
    bar.addEventListener("click", (event) => event.stopPropagation());
    frameLane.append(bar);
  }
}

function grip(item, edge) {
  const handle = document.createElement("i");
  handle.className = `grip ${edge}`;
  handle.addEventListener("pointerdown", (event) => {
    event.stopPropagation();
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    const edits = frameOf(current);
    const list = edits.zooms.includes(item) ? edits.zooms : edits.hands;
    const onMove = (moveEvent) => {
      const at = timeAt(moveEvent.clientX);
      const others = list.filter((other) => other !== item);
      if (edge === "start") {
        const floor = Math.max(span.start, ...others.filter((o) => o.end <= item.start).map((o) => o.end));
        item.start = round(clamp(at, floor, item.end - MIN_RANGE));
      } else {
        const ceiling = Math.min(span.end, ...others.filter((o) => o.start >= item.end).map((o) => o.start));
        item.end = round(clamp(at, item.start + MIN_RANGE, ceiling));
      }
      frameChanged();
    };
    const onUp = () => {
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
      drawFrameEditor(current);
    };
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
  });
  handle.addEventListener("click", (event) => event.stopPropagation());
  return handle;
}

// --- What the preview draws at a source time ---

// The zoom in force at `seconds` and how far in (0 to 1), or null. In a
// block with zooms of its own, only those, in seconds from its start, as
// the renderer has it (ADR 0034).
function activeZoom(seconds) {
  if (!current) {
    return null;
  }
  const block = playingSourceBlock(seconds);
  const own = Boolean(block && block.zooms.length);
  const at = own ? seconds - block.start : seconds;
  for (const zoom of own ? block.zooms : frameOf(current).zooms) {
    if (at >= zoom.start && at <= zoom.end) {
      let weight = 1;
      if (zoom.smooth) {
        const ease = Math.min(ZOOM_EASE_SECONDS, (zoom.end - zoom.start) / 2);
        weight = clamp(Math.min((at - zoom.start) / ease, (zoom.end - at) / ease), 0, 1);
      }
      return { zoom, weight };
    }
  }
  return null;
}

function activeHand(seconds) {
  if (!current) {
    return null;
  }
  const block = playingSourceBlock(seconds);
  if (block && block.hand_crops.length) {
    const at = seconds - block.start;
    return block.hand_crops.find((hand) => at >= hand.start && at <= hand.end) || null;
  }
  return frameOf(current).hands.find((hand) => seconds >= hand.start && seconds <= hand.end) || null;
}

// Called by app.js when a clip is chosen, saved or reset.
function resetFrameEditor(clip, clear = false) {
  if (clear) {
    frameEdits.set(clip.key, { zooms: [], hands: [] });
  }
  drawFrameEditor(clip);
}
