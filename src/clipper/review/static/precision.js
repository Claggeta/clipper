// Trimming aids (ADR 0031, V7 item 9d): the waveform and the words under
// the film strip, and handles and cuts that snap to word edges. Loaded
// after app.js, whose state it uses; app.js calls drawLanes, ensureWords
// and snap.
"use strict";

// The server's levels per second (adapters/media/peaks.py).
const PEAKS_PER_SECOND = 20;
// A handle or cut moves to a word edge at most this far away, in seconds.
const SNAP_SECONDS = 0.3;
// The words lane shows at this many seconds per frame or closer.
const WORDS_STEP = 1;
// Words are fetched for the view, or around the span when the view is
// wider than this, plus this margin either side.
const WORDS_VIEW_LIMIT = 1800;
const WORDS_MARGIN = 60;
const SNAP_KEY = "clipper-review-snap";

const waveCanvas = document.getElementById("wave");
const wordsLane = document.getElementById("words");
const snapButton = document.getElementById("snap");

let peaks = null; // Uint8Array of levels, or null until loaded
let peaksScale = 255; // the level drawn full height
let peaksState = "Waveform loading…";
let words = []; // [start, end, text], sorted by start
const wordRanges = []; // the [start, end] spans fetched so far
let wordsPending = false;
let wordsMissing = false; // the folder has no transcript: nothing to snap to

// --- The waveform ---

async function loadPeaks() {
  try {
    const response = await fetch(`/peaks/${framesKey}.bin`);
    if (!response.ok) {
      const message = await response.text().catch(() => "");
      throw new Error(message || `the server answered ${response.status}`);
    }
    peaks = new Uint8Array(await response.arrayBuffer());
    // Scale to a loud moment, not the single loudest, so speech shows.
    const sorted = Uint8Array.from(peaks).sort();
    peaksScale = Math.max(sorted[Math.floor(sorted.length * 0.995)] || 0, 1);
    peaksState = peaks.length ? "" : "No audio levels in this video";
  } catch (error) {
    peaksState = `No waveform: ${error.message}`;
  }
  if (current) {
    drawWave();
  }
}

function drawWave() {
  const width = waveCanvas.clientWidth;
  const height = waveCanvas.clientHeight;
  const ratio = window.devicePixelRatio || 1;
  waveCanvas.width = Math.round(width * ratio);
  waveCanvas.height = Math.round(height * ratio);
  const context = waveCanvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);
  const style = getComputedStyle(document.documentElement);
  if (!peaks || !peaks.length) {
    context.fillStyle = style.getPropertyValue("--muted");
    context.font = "11px system-ui, sans-serif";
    context.fillText(peaksState, 8, height / 2 + 4);
    return;
  }
  context.fillStyle = style.getPropertyValue("--wave");
  const perPixel = (viewEnd() - view.start) / width;
  for (let x = 0; x < width; x += 1) {
    const from = view.start + x * perPixel;
    const first = Math.floor(from * PEAKS_PER_SECOND);
    const last = Math.max(first + 1, Math.floor((from + perPixel) * PEAKS_PER_SECOND));
    if (first < 0 || first >= peaks.length) {
      continue;
    }
    let level = 0;
    for (let i = first; i < last && i < peaks.length; i += 1) {
      level = Math.max(level, peaks[i]);
    }
    // A square root, so quiet speech is still visible beside shouting.
    const bar = Math.max(Math.min(Math.sqrt(level / peaksScale), 1) * (height - 2), 1);
    context.fillRect(x, (height - bar) / 2, 1, bar);
  }
}

// --- Words ---

function wordsCovered(start, end) {
  return wordRanges.some(([from, to]) => from <= start && end <= to);
}

// Fetch the words for `start`..`end` unless already fetched; redraws the
// lanes when they arrive.
async function ensureWords(start, end) {
  const from = Math.max(start - WORDS_MARGIN, 0);
  const to = Math.min(end + WORDS_MARGIN, duration);
  if (wordsMissing || wordsPending || from >= to || wordsCovered(start, Math.min(end, duration))) {
    return;
  }
  wordsPending = true;
  try {
    const response = await fetch(`/api/words?start=${from}&end=${to}`);
    const data = await response.json().catch(() => ({}));
    if (response.status === 409) {
      wordsMissing = true;
      return;
    }
    if (!response.ok) {
      return;
    }
    const known = new Set(words.map(([wordStart]) => wordStart));
    words = words
      .concat(data.words.filter(([wordStart]) => !known.has(wordStart)))
      .sort((a, b) => a[0] - b[0]);
    wordRanges.push([from, to]);
  } finally {
    wordsPending = false;
  }
  if (current) {
    drawLanes();
  }
}

function drawWords() {
  const shown = view.step <= WORDS_STEP && !wordsMissing;
  wordsLane.hidden = !shown;
  wordsLane.replaceChildren();
  if (!shown) {
    return;
  }
  for (const [start, end, word] of words) {
    if (end < view.start || start > viewEnd()) {
      continue;
    }
    const chip = document.createElement("span");
    chip.className = "word";
    chip.textContent = word;
    chip.title = `${fmtTenths(start)} – ${fmtTenths(end)}`;
    chip.style.left = `${percent(start)}%`;
    chip.style.width = `${percent(end) - percent(start)}%`;
    wordsLane.append(chip);
  }
}

// Called by drawStrip after the frames.
function drawLanes() {
  if (!current || !span) {
    return;
  }
  if (viewEnd() - view.start <= WORDS_VIEW_LIMIT) {
    ensureWords(view.start, viewEnd());
  } else {
    ensureWords(span.start, span.end);
  }
  drawWave();
  drawWords();
}

// --- Snapping ---

function readSnap() {
  try {
    return localStorage.getItem(SNAP_KEY) !== "off";
  } catch {
    return true;
  }
}

let snapOn = readSnap();

function drawSnap() {
  snapButton.setAttribute("aria-pressed", String(snapOn));
}

snapButton.addEventListener("click", () => {
  snapOn = !snapOn;
  try {
    localStorage.setItem(SNAP_KEY, snapOn ? "on" : "off");
  } catch {
    // Storage is blocked; the choice lasts until the page reloads.
  }
  drawSnap();
});

// The nearest word start (`edge` "start") or end ("end") within
// SNAP_SECONDS of `seconds`, or `seconds` itself: when snapping is off,
// `free` (Alt held while dragging), or there is no word near.
function snap(seconds, edge, free = false) {
  if (!snapOn || free) {
    return seconds;
  }
  let best = seconds;
  let gap = SNAP_SECONDS;
  for (const [start, end] of words) {
    const edgeTime = edge === "start" ? start : end;
    const distance = Math.abs(edgeTime - seconds);
    if (distance <= gap) {
      gap = distance;
      best = edgeTime;
    }
  }
  return best;
}

drawSnap();
loaded.then(loadPeaks);
