// Summarize (ADR 0032, V7 item 9k): join the VOD's kept clips, as saved, in
// VOD order into one longer Short with optional label cards and music. A
// dialog picks the clips and makes it; the summary then has its own entry
// above the clip list to play, title, rate, thumbnail or trash. Loaded
// after app.js and audio.js.
"use strict";

const summaryDialog = document.getElementById("summary-dialog");
const summaryPick = document.getElementById("summary-pick");
const summaryResult = document.getElementById("summary-result");
const summaryRows = document.getElementById("summary-rows");
const summaryTotal = document.getElementById("summary-total");
const summaryMusic = document.getElementById("summary-music");
const summaryNotice = document.getElementById("summary-notice");
const summaryVideo = document.getElementById("summary-video");
const summaryEntry = document.getElementById("summary-entry");
// About the longest a YouTube Short runs (models.py SUMMARY_LIMIT_SECONDS).
const SUMMARY_LIMIT = 180;
// rank -> {on, label}, kept while the page is open
const summaryChoice = new Map();
let summaryVersion = 0;
let summaryBusy = false;
let summaryTrashArmed = null;

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

// The clips a summary can take: rendered and not trashed, in VOD order.
function summaryClips() {
  return listedClips()
    .filter((clip) => !clip.isNew && clip.file && clip.reviewed !== "trashed")
    .sort((a, b) => a.start - b.start);
}

function choiceOf(clip) {
  if (!summaryChoice.has(clip.rank)) {
    const part = folderSummary && folderSummary.parts.find((p) => p.rank === clip.rank);
    summaryChoice.set(clip.rank, {
      on: folderSummary ? Boolean(part) : clip.reviewed === "saved",
      label: part ? part.label : "",
    });
  }
  return summaryChoice.get(clip.rank);
}

function drawSummaryTotal() {
  const total = summaryClips()
    .filter((clip) => choiceOf(clip).on)
    .reduce((sum, clip) => sum + savedLength(clip), 0);
  summaryTotal.textContent = `${fmtTenths(total)} in all`;
  summaryTotal.classList.toggle("warn", total > SUMMARY_LIMIT);
  summaryTotal.title =
    total > SUMMARY_LIMIT ? "Longer than a Short can be; drop or trim a clip" : "";
  document.getElementById("summary-make").disabled = total === 0 || summaryBusy;
}

function drawSummaryPick() {
  summaryRows.replaceChildren();
  const clips = summaryClips();
  for (const clip of clips) {
    const choice = choiceOf(clip);
    const row = document.createElement("div");
    row.className = "summary-row";
    const tick = document.createElement("input");
    tick.type = "checkbox";
    tick.checked = choice.on;
    tick.setAttribute("aria-label", `Include clip ${clip.rank}`);
    tick.addEventListener("change", () => {
      choice.on = tick.checked;
      drawSummaryTotal();
    });
    const name = document.createElement("span");
    name.className = "summary-name";
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
      flag.className = "warn summary-flag";
      flag.textContent = "unsaved edits";
      flag.title = "The summary uses the saved file; save this clip first to include your edits";
      row.append(flag);
    }
    summaryRows.append(row);
  }
  if (!clips.length) {
    summaryRows.textContent = "No saved clips to join yet.";
  }
  summaryMusic.replaceChildren();
  for (const [value, text] of [["", "No music"], ...audioFiles.music.map((n) => [n, n])]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = text;
    summaryMusic.append(option);
  }
  summaryMusic.value = (folderSummary && folderSummary.music && folderSummary.music.file) || "";
  drawSummaryTotal();
}

document.getElementById("summary-hours").addEventListener("click", () => {
  for (const clip of summaryClips()) {
    choiceOf(clip).label = `Hour ${Math.floor(clip.start / 3600) + 1}`;
  }
  drawSummaryPick();
});

document.getElementById("summary-make").addEventListener("click", async () => {
  const parts = summaryClips()
    .filter((clip) => choiceOf(clip).on)
    .map((clip) => ({ rank: clip.rank, label: choiceOf(clip).label.trim() }));
  const music = summaryMusic.value
    ? {
        file: summaryMusic.value,
        level_db: Number(document.getElementById("summary-level").value),
        duck: document.getElementById("summary-duck").checked,
      }
    : null;
  summaryBusy = true;
  drawSummaryTotal();
  const total = parts.reduce((sum, p) => sum + savedLength(clipsByKey.get(p.rank)), 0);
  summaryNotice.classList.remove("error");
  summaryNotice.textContent = `Rendering ${fmtTenths(total)}; about ${Math.ceil(total / 3)} s.`;
  try {
    folderSummary = await post("/api/summary", { parts, music });
    summaryVersion += 1;
    summaryNotice.textContent = "";
    drawSummaryEntry();
    showSummaryResult();
  } catch (error) {
    summaryNotice.textContent = error.message;
    summaryNotice.classList.add("error");
  } finally {
    summaryBusy = false;
    drawSummaryTotal();
  }
});

// --- The made summary ---

function showSummaryPick() {
  summaryResult.hidden = true;
  summaryPick.hidden = false;
  drawSummaryPick();
}

function showSummaryResult() {
  if (!folderSummary) {
    showSummaryPick();
    return;
  }
  summaryPick.hidden = true;
  summaryResult.hidden = false;
  const src = `/clips/${encodeURIComponent(folderSummary.file)}?v=${summaryVersion}`;
  if (summaryVideo.getAttribute("src") !== src) {
    summaryVideo.setAttribute("src", src);
  }
  document.getElementById("summary-title").value = folderSummary.title || "";
  for (const button of document.querySelectorAll("#summary-rating button")) {
    button.setAttribute("aria-pressed", String(button.dataset.value === folderSummary.rating));
  }
  document.getElementById("summary-length").textContent =
    `${fmtTenths(folderSummary.duration)} · ${folderSummary.parts.length} clips`;
  const thumb = document.getElementById("summary-thumb");
  thumb.hidden = !folderSummary.thumbnail;
  if (folderSummary.thumbnail) {
    thumb.src = `/thumbs/${encodeURIComponent(folderSummary.thumbnail)}?v=${Date.now()}`;
  }
}

function summaryResultNotice(message, failed = false) {
  const line = document.getElementById("summary-result-notice");
  line.textContent = message;
  line.classList.toggle("error", failed);
}

async function saveSummary(rating) {
  try {
    folderSummary = await post("/api/summary/save", {
      title: document.getElementById("summary-title").value,
      rating,
    });
    summaryResultNotice("Saved.");
    showSummaryResult();
    drawSummaryEntry();
  } catch (error) {
    summaryResultNotice(error.message, true);
  }
}

for (const button of document.querySelectorAll("#summary-rating button")) {
  button.addEventListener("click", () => {
    const value = folderSummary.rating === button.dataset.value ? null : button.dataset.value;
    saveSummary(value);
  });
}

document.getElementById("summary-save").addEventListener("click", () => {
  saveSummary(folderSummary.rating);
});

document.getElementById("summary-suggest").addEventListener("click", async () => {
  summaryResultNotice("Asking the local model…");
  try {
    const reply = await post("/api/summary/suggest", {});
    document.getElementById("summary-title").value = reply.title;
    summaryResultNotice(`Suggested; hook: ${reply.hook}`);
  } catch (error) {
    summaryResultNotice(error.message, true);
  }
});

document.getElementById("summary-thumb-make").addEventListener("click", async () => {
  summaryResultNotice("Making the thumbnail…");
  try {
    folderSummary = await post("/api/summary/thumbnail", {
      at: summaryVideo.currentTime || 0,
      text: document.getElementById("summary-thumb-text").value,
      place: "middle",
    });
    summaryResultNotice(`Saved as ${folderSummary.thumbnail}.`);
    showSummaryResult();
  } catch (error) {
    summaryResultNotice(error.message, true);
  }
});

document.getElementById("summary-remake").addEventListener("click", showSummaryPick);

document.getElementById("summary-trash").addEventListener("click", async () => {
  const button = document.getElementById("summary-trash");
  if (summaryTrashArmed === null) {
    button.textContent = "Confirm trash";
    button.classList.add("armed");
    summaryTrashArmed = setTimeout(() => {
      summaryTrashArmed = null;
      button.textContent = "Trash summary";
      button.classList.remove("armed");
    }, CONFIRM_MS);
    return;
  }
  clearTimeout(summaryTrashArmed);
  summaryTrashArmed = null;
  button.textContent = "Trash summary";
  button.classList.remove("armed");
  try {
    await post("/api/summary/trash", {});
    folderSummary = null;
    summaryChoice.clear();
    summaryVideo.removeAttribute("src");
    drawSummaryEntry();
    showSummaryPick();
  } catch (error) {
    summaryResultNotice(error.message, true);
  }
});

// --- Opening and the list entry ---

function openSummary(result) {
  summaryDialog.hidden = false;
  if (result && folderSummary) {
    showSummaryResult();
  } else {
    showSummaryPick();
  }
}

function closeSummary() {
  summaryVideo.pause();
  summaryDialog.hidden = true;
}

document.getElementById("summarize").addEventListener("click", () => openSummary(false));
document.getElementById("summary-close").addEventListener("click", closeSummary);
summaryEntry.addEventListener("click", () => openSummary(true));
// Esc closes the dialog wherever the focus is; keys.js leaves keys alone
// while it is open.
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !summaryDialog.hidden) {
    closeSummary();
  }
});

function drawSummaryEntry() {
  summaryEntry.hidden = !folderSummary;
  if (folderSummary) {
    const rating = folderSummary.rating ? ` · ${folderSummary.rating}` : "";
    summaryEntry.querySelector(".name").textContent = folderSummary.title || "Summary";
    summaryEntry.querySelector(".span").textContent =
      `${fmtTenths(folderSummary.duration)} · ${folderSummary.parts.length} clips${rating}`;
  }
}

loaded.then(drawSummaryEntry);
