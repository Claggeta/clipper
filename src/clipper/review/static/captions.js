// Caption editing (ADR 0017, caption editing amendment; V7 item 9f): the
// span's words as chips in the chunks they will show in. Click a chip to
// fix its text (empty hides it), right-click or press K on it to make it a
// key word, and pick the highlight colour. Edits are kept per clip until
// Save, which re-renders the clip. Loaded after app.js and before
// preview.js, which draws them.
"use strict";

const captionWordsBox = document.getElementById("caption-words");
const highlightBox = document.getElementById("highlights");
const captionNotice = document.getElementById("caption-notice");
// key -> {fixes: Map(start ms -> text), keys: Set(start ms), highlight}
const captionEdits = new Map();
// Wait this long after a span or edit change before asking the server.
const EDITOR_WAIT_MS = 300;
let editorKey = ""; // what the chips were drawn for
let editorTimer = 0;

function toMs(seconds) {
  return Math.round(seconds * 1000);
}

// What the clip's file and label hold, or null.
function savedCaptionEdits(clip) {
  return clip.caption_edits || (clip.judgment && clip.judgment.caption_edits) || null;
}

function editsOf(clip) {
  if (!captionEdits.has(clip.key)) {
    const saved = savedCaptionEdits(clip) || {};
    captionEdits.set(clip.key, {
      fixes: new Map((saved.fixes || []).map((fix) => [toMs(fix.start_seconds), fix.text])),
      keys: new Set((saved.keys || []).map(toMs)),
      highlight: saved.highlight || "yellow",
    });
  }
  return captionEdits.get(clip.key);
}

// The edits as the server takes them, sorted; null when they change nothing.
function sentCaptionEdits(clip) {
  const edits = editsOf(clip);
  const fixes = [...edits.fixes]
    .sort((a, b) => a[0] - b[0])
    .map(([start, text]) => ({ start_seconds: start / 1000, text }));
  const keys = [...edits.keys].sort((a, b) => a - b).map((start) => start / 1000);
  if (!fixes.length && !keys.length && edits.highlight === "yellow") {
    return null;
  }
  return { fixes, keys, highlight: edits.highlight };
}

function captionEditsText(edits) {
  if (!edits) {
    return "null";
  }
  const fixes = [...(edits.fixes || [])]
    .map((fix) => [toMs(fix.start_seconds), fix.text])
    .sort((a, b) => a[0] - b[0]);
  const keys = [...(edits.keys || [])].map(toMs).sort((a, b) => a - b);
  return JSON.stringify([fixes, keys, edits.highlight || "yellow"]);
}

function sameCaptionEdits(a, b) {
  return captionEditsText(a) === captionEditsText(b);
}

// For the server's caption routes: `&edits=...`, or "" with none.
function captionEditsQuery(clip) {
  const edits = sentCaptionEdits(clip);
  return edits ? `&edits=${encodeURIComponent(JSON.stringify(edits))}` : "";
}

function captionsChanged() {
  drawUnsaved();
  scheduleEditor();
}

// --- The chips ---

function editorSpanKey() {
  return `${current.key}|${span.start}|${span.end}|${captionEditsText(sentCaptionEdits(current))}|${activeCuts().join(";")}`;
}

// Redraw the chips a moment after the span, cuts or edits change.
function scheduleEditor() {
  if (!current || !span) {
    return;
  }
  clearTimeout(editorTimer);
  editorTimer = setTimeout(drawCaptionEditor, EDITOR_WAIT_MS);
}

async function fetchJson(url) {
  const response = await fetch(url);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error || `the server answered ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return data;
}

async function drawCaptionEditor() {
  if (!current || !span) {
    return;
  }
  const clip = current;
  const key = editorSpanKey();
  if (key === editorKey) {
    drawHighlights(clip);
    return;
  }
  const query = `start=${span.start}&end=${span.end}`;
  const cuts = activeCuts().map(([start, end]) => `${start}:${end}`).join(",");
  let words;
  let chunks;
  try {
    [words, chunks] = await Promise.all([
      fetchJson(`/api/words?${query}`).then((data) => data.words),
      fetchJson(
        `/api/captions?${query}${cuts ? `&cuts=${cuts}` : ""}${captionEditsQuery(clip)}`,
      ).then((data) => data.chunks),
    ]);
  } catch (error) {
    if (clip === current) {
      captionWordsBox.replaceChildren();
      captionNotice.textContent =
        error.status === 409 ? "This folder has no captions to edit." : error.message;
    }
    return;
  }
  if (clip !== current || key !== editorSpanKey()) {
    return;
  }
  editorKey = key;
  drawChips(clip, words, chunks);
  drawHighlights(clip);
}

function drawChips(clip, words, chunks) {
  const edits = editsOf(clip);
  // Which chunk each shown word is in, by its start.
  const chunkOf = new Map();
  chunks.forEach((chunk, index) => {
    for (const [start] of chunk) {
      chunkOf.set(toMs(start), index);
    }
  });
  captionWordsBox.replaceChildren();
  captionNotice.textContent = words.length
    ? "Click a word to fix it (empty hides it); right-click or K for a key word."
    : "No word times in this span, so nothing to edit.";
  let group = null;
  let groupIndex = -1;
  for (const [start, end, original] of words) {
    if (start < span.start || start >= span.end) {
      continue;
    }
    const at = toMs(start);
    const index = chunkOf.has(at) ? chunkOf.get(at) : groupIndex;
    if (!group || index !== groupIndex) {
      group = document.createElement("span");
      group.className = "chunk";
      captionWordsBox.append(group);
      groupIndex = index;
    }
    group.append(wordChip(clip, edits, at, end, original, chunkOf.has(at)));
  }
}

function wordChip(clip, edits, at, end, original, shown) {
  const chip = document.createElement("button");
  chip.type = "button";
  chip.className = "word-chip";
  const fixed = edits.fixes.has(at) ? edits.fixes.get(at) : null;
  const hidden = fixed === "";
  chip.textContent = hidden ? original : (fixed ?? original);
  chip.classList.toggle("fixed", fixed !== null && !hidden);
  chip.classList.toggle("hidden-word", hidden);
  chip.classList.toggle("key", edits.keys.has(at));
  chip.classList.toggle("unshown", !shown && !hidden);
  chip.title =
    `${fmtTenths(at / 1000)} – ${fmtTenths(end)}` +
    (fixed !== null ? ` · was "${original}"` : "") +
    (!shown && !hidden ? " · in a cut, not shown" : "");
  chip.style.setProperty("--highlight", previewLayout.highlights[edits.highlight]);
  chip.addEventListener("click", () => editChip(clip, chip, at, original));
  chip.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    toggleKey(clip, at);
  });
  chip.addEventListener("keydown", (event) => {
    if (event.key.toLowerCase() === "k" && !event.ctrlKey && !event.metaKey && !event.altKey) {
      // Not the page's play key while a word is focused.
      event.preventDefault();
      event.stopPropagation();
      toggleKey(clip, at);
    }
  });
  return chip;
}

function toggleKey(clip, at) {
  const edits = editsOf(clip);
  if (edits.keys.has(at)) {
    edits.keys.delete(at);
  } else {
    edits.keys.add(at);
  }
  captionsChanged();
}

// Swap the chip for a box; Enter or leaving it keeps the text, Esc drops it.
function editChip(clip, chip, at, original) {
  const edits = editsOf(clip);
  const box = document.createElement("input");
  box.type = "text";
  box.className = "word-box";
  box.maxLength = 40;
  box.value = edits.fixes.has(at) ? edits.fixes.get(at) : original;
  box.setAttribute("aria-label", `Fix "${original}"`);
  let done = false;
  const finish = (keep) => {
    if (done) {
      return;
    }
    done = true;
    if (keep) {
      const text = box.value.trim().split(/\s+/).join(" ");
      if (text === original.trim()) {
        edits.fixes.delete(at);
      } else {
        edits.fixes.set(at, text);
      }
    }
    editorKey = "";
    captionsChanged();
  };
  box.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      finish(true);
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      finish(false);
    }
  });
  box.addEventListener("blur", () => finish(true));
  chip.replaceWith(box);
  box.focus();
  box.select();
}

// --- The highlight colour ---

function drawHighlights(clip) {
  const edits = editsOf(clip);
  highlightBox.replaceChildren();
  for (const [name, colour] of Object.entries(previewLayout.highlights)) {
    const swatch = document.createElement("button");
    swatch.type = "button";
    swatch.className = "swatch";
    swatch.style.background = colour;
    swatch.title = name;
    swatch.setAttribute("aria-label", name);
    swatch.setAttribute("aria-pressed", String(edits.highlight === name));
    swatch.addEventListener("click", () => {
      edits.highlight = name;
      editorKey = "";
      captionsChanged();
    });
    highlightBox.append(swatch);
  }
}

// Called by app.js when a clip is chosen, saved or reset.
function resetCaptionEditor(clip, clear = false) {
  if (clear) {
    captionEdits.set(clip.key, { fixes: new Map(), keys: new Set(), highlight: "yellow" });
  }
  editorKey = "";
  captionWordsBox.replaceChildren();
  captionNotice.textContent = "";
  scheduleEditor();
}
