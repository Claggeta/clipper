// Combine (ADR 0032, V7 item 9k): join the VOD's kept clips, as saved, in
// VOD order into one longer Short with optional label cards and music. A
// dialog picks the clips and makes it; the combined clip then has its own entry
// above the clip list to play, title, rate, thumbnail or trash. Loaded
// after app.js and audio.js.
"use strict";

const combineDialog = document.getElementById("combine-dialog");
const combinePick = document.getElementById("combine-pick");
const combineResult = document.getElementById("combine-result");
const combineRows = document.getElementById("combine-rows");
const combineTotal = document.getElementById("combine-total");
const combineMusic = document.getElementById("combine-music");
const combineNotice = document.getElementById("combine-notice");
const combineVideo = document.getElementById("combine-video");
const combineEntry = document.getElementById("combine-entry");
// About the longest a YouTube Short runs (models.py SHORTS_LIMIT_SECONDS).
const SHORTS_LIMIT = 180;
// rank -> {on, label}, kept while the page is open
const combineChoice = new Map();
let combineVersion = 0;
let combineBusy = false;
let combineTrashArmed = null;

// A clip's length as saved: its span less its saved cuts.
function savedLength(clip) {
  return clip.end - clip.start - (clip.cuts || []).reduce((sum, [a, b]) => sum + b - a, 0);
}

// Whether the page holds changes to a clip that its file does not have.
function clipChanged(clip) {
  const saved = savedState(clip);
  const edit = edits.get(clip.key);
  const shown = edit || saved;
  if (edit && (!sameTime(edit.start, saved.start) || !sameTime(edit.end, saved.end))) {
    return true;
  }
  if (cutEdits.has(clip.key)) {
    const on = cutsOf(clip)
      .filter((cut) => cut.on && cut.start > shown.start && cut.end < shown.end)
      .map((cut) => [cut.start, cut.end]);
    if (!sameCuts(on, savedCuts(clip))) {
      return true;
    }
  }
  return (
    (textEdits.has(clip.key) && !sameTexts(sentTexts(clip), savedTexts(clip))) ||
    (captionEdits.has(clip.key) &&
      !sameCaptionEdits(sentCaptionEdits(clip), savedCaptionEdits(clip))) ||
    (frameEdits.has(clip.key) && !sameFrame(clip)) ||
    (audioEdits.has(clip.key) && !sameAudio(clip)) ||
    (chosen.has(clip.key) && chosen.get(clip.key) !== (clip.style || null))
  );
}

// The clips a combined clip can take: rendered and not trashed, in VOD order.
function combineClips() {
  return listedClips()
    .filter((clip) => !clip.isNew && clip.file && clip.reviewed !== "trashed")
    .sort((a, b) => a.start - b.start);
}

function choiceOf(clip) {
  if (!combineChoice.has(clip.rank)) {
    const part = folderCombined && folderCombined.parts.find((p) => p.rank === clip.rank);
    combineChoice.set(clip.rank, {
      on: folderCombined ? Boolean(part) : clip.reviewed === "saved",
      label: part ? part.label : "",
    });
  }
  return combineChoice.get(clip.rank);
}

function drawCombinedTotal() {
  const total = combineClips()
    .filter((clip) => choiceOf(clip).on)
    .reduce((sum, clip) => sum + savedLength(clip), 0);
  combineTotal.textContent = `${fmtTenths(total)} in all`;
  combineTotal.classList.toggle("warn", total > SHORTS_LIMIT);
  combineTotal.title =
    total > SHORTS_LIMIT ? "Longer than a Short can be; drop or trim a clip" : "";
  document.getElementById("combine-make").disabled = total === 0 || combineBusy;
}

function drawCombinedPick() {
  combineRows.replaceChildren();
  const clips = combineClips();
  for (const clip of clips) {
    const choice = choiceOf(clip);
    const row = document.createElement("div");
    row.className = "combine-row";
    const tick = document.createElement("input");
    tick.type = "checkbox";
    tick.checked = choice.on;
    tick.setAttribute("aria-label", `Include clip ${clip.rank}`);
    tick.addEventListener("change", () => {
      choice.on = tick.checked;
      drawCombinedTotal();
    });
    const name = document.createElement("span");
    name.className = "combine-name";
    name.textContent = `${clip.rank}. ${clipName(clip)}`;
    name.title = `${fmt(clip.start)} in the VOD`;
    const length = document.createElement("span");
    length.className = "muted";
    length.textContent = `${savedLength(clip).toFixed(1)} s`;
    const label = document.createElement("input");
    label.type = "text";
    label.maxLength = 60;
    label.placeholder = "Card (optional)";
    label.value = choice.label;
    label.addEventListener("input", () => {
      choice.label = label.value;
    });
    row.append(tick, name, length, label);
    if (clipChanged(clip)) {
      const flag = document.createElement("span");
      flag.className = "warn combine-flag";
      flag.textContent = "unsaved edits";
      flag.title = "The combined clip uses the saved file; save this clip first to include your edits";
      row.append(flag);
    }
    combineRows.append(row);
  }
  if (!clips.length) {
    combineRows.textContent = "No saved clips to join yet.";
  }
  combineMusic.replaceChildren();
  for (const [value, text] of [["", "No music"], ...audioFiles.music.map((n) => [n, n])]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = text;
    combineMusic.append(option);
  }
  combineMusic.value = (folderCombined && folderCombined.music && folderCombined.music.file) || "";
  drawCombinedTotal();
}

document.getElementById("combine-hours").addEventListener("click", () => {
  for (const clip of combineClips()) {
    choiceOf(clip).label = `Hour ${Math.floor(clip.start / 3600) + 1}`;
  }
  drawCombinedPick();
});

document.getElementById("combine-make").addEventListener("click", async () => {
  const parts = combineClips()
    .filter((clip) => choiceOf(clip).on)
    .map((clip) => ({ rank: clip.rank, label: choiceOf(clip).label.trim() }));
  const music = combineMusic.value
    ? {
        file: combineMusic.value,
        level_db: Number(document.getElementById("combine-level").value),
        duck: document.getElementById("combine-duck").checked,
      }
    : null;
  combineBusy = true;
  drawCombinedTotal();
  const total = parts.reduce((sum, p) => sum + savedLength(clipsByKey.get(p.rank)), 0);
  combineNotice.classList.remove("error");
  combineNotice.textContent = `Rendering ${fmtTenths(total)}; about ${Math.ceil(total / 3)} s.`;
  try {
    folderCombined = await post("/api/combined", { parts, music });
    combineVersion += 1;
    combineNotice.textContent = "";
    drawCombinedEntry();
    showCombinedResult();
  } catch (error) {
    combineNotice.textContent = error.message;
    combineNotice.classList.add("error");
  } finally {
    combineBusy = false;
    drawCombinedTotal();
  }
});

// --- The made combined ---

function showCombinedPick() {
  combineResult.hidden = true;
  combinePick.hidden = false;
  drawCombinedPick();
}

function showCombinedResult() {
  if (!folderCombined) {
    showCombinedPick();
    return;
  }
  combinePick.hidden = true;
  combineResult.hidden = false;
  const src = `/clips/${encodeURIComponent(folderCombined.file)}?v=${combineVersion}`;
  if (combineVideo.getAttribute("src") !== src) {
    combineVideo.setAttribute("src", src);
  }
  document.getElementById("combine-title").value = folderCombined.title || "";
  for (const button of document.querySelectorAll("#combine-rating button")) {
    button.setAttribute("aria-pressed", String(button.dataset.value === folderCombined.rating));
  }
  document.getElementById("combine-length").textContent =
    `${fmtTenths(folderCombined.duration)} · ${folderCombined.parts.length} clips`;
  const thumb = document.getElementById("combine-thumb");
  thumb.hidden = !folderCombined.thumbnail;
  if (folderCombined.thumbnail) {
    thumb.src = `/thumbs/${encodeURIComponent(folderCombined.thumbnail)}?v=${Date.now()}`;
  }
}

function combineResultNotice(message, failed = false) {
  const line = document.getElementById("combine-result-notice");
  line.textContent = message;
  line.classList.toggle("error", failed);
}

async function saveCombined(rating) {
  try {
    folderCombined = await post("/api/combined/save", {
      title: document.getElementById("combine-title").value,
      rating,
    });
    combineResultNotice("Saved.");
    showCombinedResult();
    drawCombinedEntry();
  } catch (error) {
    combineResultNotice(error.message, true);
  }
}

for (const button of document.querySelectorAll("#combine-rating button")) {
  button.addEventListener("click", () => {
    const value = folderCombined.rating === button.dataset.value ? null : button.dataset.value;
    saveCombined(value);
  });
}

document.getElementById("combine-save").addEventListener("click", () => {
  saveCombined(folderCombined.rating);
});

document.getElementById("combine-suggest").addEventListener("click", async () => {
  combineResultNotice("Asking the local model…");
  try {
    const reply = await post("/api/combined/suggest", {});
    document.getElementById("combine-title").value = reply.title;
    combineResultNotice(`Suggested; hook: ${reply.hook}`);
  } catch (error) {
    combineResultNotice(error.message, true);
  }
});

document.getElementById("combine-thumb-make").addEventListener("click", async () => {
  combineResultNotice("Making the thumbnail…");
  try {
    folderCombined = await post("/api/combined/thumbnail", {
      at: combineVideo.currentTime || 0,
      text: document.getElementById("combine-thumb-text").value,
      place: "middle",
    });
    combineResultNotice(`Saved as ${folderCombined.thumbnail}.`);
    showCombinedResult();
  } catch (error) {
    combineResultNotice(error.message, true);
  }
});

document.getElementById("combine-remake").addEventListener("click", showCombinedPick);

document.getElementById("combine-trash").addEventListener("click", async () => {
  const button = document.getElementById("combine-trash");
  if (combineTrashArmed === null) {
    button.textContent = "Confirm trash";
    button.classList.add("armed");
    combineTrashArmed = setTimeout(() => {
      combineTrashArmed = null;
      button.textContent = "Trash combined clip";
      button.classList.remove("armed");
    }, CONFIRM_MS);
    return;
  }
  clearTimeout(combineTrashArmed);
  combineTrashArmed = null;
  button.textContent = "Trash combined clip";
  button.classList.remove("armed");
  try {
    await post("/api/combined/trash", {});
    folderCombined = null;
    combineChoice.clear();
    combineVideo.removeAttribute("src");
    drawCombinedEntry();
    showCombinedPick();
  } catch (error) {
    combineResultNotice(error.message, true);
  }
});

// --- Opening and the list entry ---

function openCombined(result) {
  combineDialog.hidden = false;
  if (result && folderCombined) {
    showCombinedResult();
  } else {
    showCombinedPick();
  }
}

function closeCombined() {
  combineVideo.pause();
  combineDialog.hidden = true;
}

document.getElementById("combine").addEventListener("click", () => openCombined(false));
document.getElementById("combine-close").addEventListener("click", closeCombined);
combineEntry.addEventListener("click", () => openCombined(true));
// Esc closes the dialog wherever the focus is; keys.js leaves keys alone
// while it is open.
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !combineDialog.hidden) {
    closeCombined();
  }
});

function drawCombinedEntry() {
  combineEntry.hidden = !folderCombined;
  if (folderCombined) {
    const rating = folderCombined.rating ? ` · ${folderCombined.rating}` : "";
    combineEntry.querySelector(".name").textContent = folderCombined.title || "Combined clip";
    combineEntry.querySelector(".span").textContent =
      `${fmtTenths(folderCombined.duration)} · ${folderCombined.parts.length} clips${rating}`;
  }
}

loaded.then(drawCombinedEntry);
