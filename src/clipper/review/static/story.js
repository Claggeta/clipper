// Summarize (ADR 0033, V7 item 9l): the VOD's story in beats. The model
// plans the beats in the background while the page shows its progress;
// the beats come back to play, nudge, re-card, remove or add to, and are
// kept as edited; Make summary cuts and joins them with their cards and
// music. The result has a Summary entry above the clip list, handled as
// Combine's is. Loaded after app.js, audio.js and combine.js.
"use strict";

const storyDialog = document.getElementById("story-dialog");
const storyPlanView = document.getElementById("story-plan");
const storyResultView = document.getElementById("story-result");
const storyRows = document.getElementById("story-rows");
const storyTotal = document.getElementById("story-total");
const storyProgress = document.getElementById("story-progress");
const storyMusic = document.getElementById("story-music");
const storyVideo = document.getElementById("story-video");
const storyEntry = document.getElementById("story-entry");
// The dialog's own player for a beat, since the dialog covers the page's.
const storyPeek = document.getElementById("story-peek");
// A new beat at the playhead lasts this long, in seconds.
const NEW_BEAT_SECONDS = 15;
// How often the page asks how the plan is going, in ms.
const STORY_POLL_MS = 1000;
// Edits are kept this long after the last change, in ms.
const STORY_SAVE_MS = 800;
let folderStory = null;
let storyBeats = []; // [{start, end, card, why}] as edited
let storyPlaying = null; // the beat whose span the source is playing
let storySaveTimer = 0;
let storyPolling = 0;
let storyVersion = 0;
let storyTrashArmed = null;
let storyBusy = false;

function storyNotice(message, failed = false) {
  storyProgress.textContent = message;
  storyProgress.classList.toggle("error", failed);
}

function storyLength() {
  return storyBeats.reduce((sum, beat) => sum + beat.end - beat.start, 0);
}

function drawStoryTotal() {
  const total = storyLength();
  storyTotal.textContent = storyBeats.length
    ? `${storyBeats.length} beats, ${fmtTenths(total)} in all`
    : "";
  storyTotal.classList.toggle("warn", total > SHORTS_LIMIT);
  document.getElementById("story-make").disabled = !storyBeats.length || storyBusy;
}

// Keep the edited beats a moment after the last change, so a reload keeps them.
function keepStoryBeats() {
  clearTimeout(storySaveTimer);
  storySaveTimer = setTimeout(async () => {
    if (!storyBeats.length) {
      return;
    }
    try {
      folderStory = await post("/api/story/beats", { beats: storyBeats });
    } catch (error) {
      storyNotice(error.message, true);
    }
  }, STORY_SAVE_MS);
}

function storyChanged() {
  storyBeats.sort((a, b) => a.start - b.start);
  drawStoryTotal();
  keepStoryBeats();
}

function nudge(beat, edge, by, label) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.title = `${edge === "start" ? "Start" : "End"} ${by > 0 ? "later" : "earlier"} by ${Math.abs(by)} s`;
  button.addEventListener("click", () => {
    const value = round(beat[edge] + by);
    if (edge === "start" && value >= 0 && value < beat.end - 1) {
      beat.start = value;
    } else if (edge === "end" && value > beat.start + 1 && value <= duration) {
      beat.end = value;
    }
    drawStoryBeats();
    storyChanged();
  });
  return button;
}

function drawStoryBeats() {
  storyRows.replaceChildren();
  storyBeats.forEach((beat, index) => {
    const row = document.createElement("div");
    row.className = "story-row";
    const play = document.createElement("button");
    play.type = "button";
    play.textContent = storyPlaying === beat ? "Stop" : "Play";
    play.addEventListener("click", () => playBeat(beat));
    const when = document.createElement("span");
    when.className = "story-when";
    when.textContent = `${fmtTenths(beat.start)} · ${(beat.end - beat.start).toFixed(1)} s`;
    when.title = beat.why || "";
    const card = document.createElement("input");
    card.type = "text";
    card.maxLength = 60;
    card.placeholder = "Card (optional)";
    card.value = beat.card;
    card.addEventListener("input", () => {
      beat.card = card.value;
      keepStoryBeats();
    });
    const edges = document.createElement("span");
    edges.className = "story-edges";
    edges.append(
      nudge(beat, "start", -1, "◀"),
      nudge(beat, "start", 1, "▶"),
      " start · end ",
      nudge(beat, "end", -1, "◀"),
      nudge(beat, "end", 1, "▶"),
    );
    row.append(
      play,
      when,
      card,
      edges,
      removeButton(() => {
        storyBeats.splice(index, 1);
        drawStoryBeats();
        storyChanged();
      }),
    );
    storyRows.append(row);
  });
  drawStoryTotal();
}

// Play one beat's span in the dialog's player, stopping at its end.
function playBeat(beat) {
  if (storyPlaying === beat) {
    storyPeek.pause();
    storyPlaying = null;
    drawStoryBeats();
    return;
  }
  storyPlaying = beat;
  storyPeek.currentTime = beat.start;
  storyPeek.play().catch(() => {});
  drawStoryBeats();
}

storyPeek.addEventListener("timeupdate", () => {
  if (storyPlaying && storyPeek.currentTime >= storyPlaying.end) {
    storyPeek.pause();
    storyPlaying = null;
    drawStoryBeats();
  }
});

document.getElementById("story-add").addEventListener("click", () => {
  // From where the dialog's player stands, else the page's playhead.
  const at = storyPeek.currentTime || player.currentTime;
  const start = round(clamp(at, 0, Math.max(duration - 1, 0)));
  const end = round(Math.min(start + NEW_BEAT_SECONDS, duration));
  if (storyBeats.some((beat) => start < beat.end && beat.start < end)) {
    storyNotice("That overlaps a beat; move the playhead first.", true);
    return;
  }
  storyBeats.push({ start, end, card: "", why: "Added by hand" });
  drawStoryBeats();
  storyChanged();
});

// --- Planning ---

async function pollStory() {
  clearTimeout(storyPolling);
  try {
    const response = await fetch("/api/story/status");
    const data = await response.json();
    if (data.state === "running") {
      storyNotice(`${data.progress}…`);
      storyPolling = setTimeout(pollStory, STORY_POLL_MS);
      return;
    }
    if (data.state === "failed") {
      storyNotice(data.error, true);
    } else if (data.state === "done") {
      storyNotice("Planned. Play, nudge or re-card the beats, then make the summary.");
    }
    if (data.story) {
      folderStory = data.story;
      storyBeats = folderStory.beats.map((beat) => ({ ...beat }));
      drawStoryBeats();
    }
    document.getElementById("story-replan").disabled = false;
  } catch (error) {
    storyNotice(error.message, true);
  }
}

async function planStory() {
  document.getElementById("story-replan").disabled = true;
  storyNotice("Starting…");
  try {
    await post("/api/story/plan", {});
    pollStory();
  } catch (error) {
    storyNotice(error.message, true);
    document.getElementById("story-replan").disabled = false;
  }
}

document.getElementById("story-replan").addEventListener("click", planStory);

document.getElementById("story-make").addEventListener("click", async () => {
  clearTimeout(storySaveTimer);
  const music = storyMusic.value
    ? {
        file: storyMusic.value,
        level_db: Number(document.getElementById("story-level").value),
        duck: document.getElementById("story-duck").checked,
      }
    : null;
  storyBusy = true;
  drawStoryTotal();
  storyNotice(`Rendering ${fmtTenths(storyLength())}; about ${Math.ceil(storyLength() / 2)} s.`);
  try {
    folderStory = await post("/api/story", { beats: storyBeats, music });
    storyVersion += 1;
    storyNotice("");
    drawStoryEntry();
    showStoryResult();
  } catch (error) {
    storyNotice(error.message, true);
  } finally {
    storyBusy = false;
    drawStoryTotal();
  }
});

// --- The made summary ---

function storyResultNotice(message, failed = false) {
  const line = document.getElementById("story-result-notice");
  line.textContent = message;
  line.classList.toggle("error", failed);
}

function showStoryPlan() {
  storyResultView.hidden = true;
  storyPlanView.hidden = false;
  storyMusic.replaceChildren();
  for (const [value, text] of [["", "No music"], ...audioFiles.music.map((n) => [n, n])]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = text;
    storyMusic.append(option);
  }
  storyMusic.value = (folderStory && folderStory.music && folderStory.music.file) || "";
  if (folderStory) {
    storyBeats = folderStory.beats.map((beat) => ({ ...beat }));
    drawStoryBeats();
  } else {
    storyBeats = [];
    drawStoryBeats();
    planStory();
  }
}

function showStoryResult() {
  storyPlanView.hidden = true;
  storyResultView.hidden = false;
  const src = `/clips/${encodeURIComponent(folderStory.file)}?v=${storyVersion}`;
  if (storyVideo.getAttribute("src") !== src) {
    storyVideo.setAttribute("src", src);
  }
  document.getElementById("story-title").value = folderStory.title || "";
  for (const button of document.querySelectorAll("#story-rating button")) {
    button.setAttribute("aria-pressed", String(button.dataset.value === folderStory.rating));
  }
  document.getElementById("story-length").textContent =
    `${fmtTenths(folderStory.duration)} · ${folderStory.beats.length} beats`;
  const thumb = document.getElementById("story-thumb");
  thumb.hidden = !folderStory.thumbnail;
  if (folderStory.thumbnail) {
    thumb.src = `/thumbs/${encodeURIComponent(folderStory.thumbnail)}?v=${Date.now()}`;
  }
}

async function saveStory(rating) {
  try {
    folderStory = await post("/api/story/save", {
      title: document.getElementById("story-title").value,
      rating,
    });
    storyResultNotice("Saved.");
    showStoryResult();
    drawStoryEntry();
  } catch (error) {
    storyResultNotice(error.message, true);
  }
}

for (const button of document.querySelectorAll("#story-rating button")) {
  button.addEventListener("click", () => {
    saveStory(folderStory.rating === button.dataset.value ? null : button.dataset.value);
  });
}

document.getElementById("story-save").addEventListener("click", () => saveStory(folderStory.rating));

document.getElementById("story-suggest").addEventListener("click", async () => {
  storyResultNotice("Asking the local model…");
  try {
    const reply = await post("/api/story/suggest", {});
    document.getElementById("story-title").value = reply.title;
    storyResultNotice(`Suggested; hook: ${reply.hook}`);
  } catch (error) {
    storyResultNotice(error.message, true);
  }
});

document.getElementById("story-thumb-make").addEventListener("click", async () => {
  storyResultNotice("Making the thumbnail…");
  try {
    folderStory = await post("/api/story/thumbnail", {
      at: storyVideo.currentTime || 0,
      text: document.getElementById("story-thumb-text").value,
      place: "middle",
    });
    storyResultNotice(`Saved as ${folderStory.thumbnail}.`);
    showStoryResult();
  } catch (error) {
    storyResultNotice(error.message, true);
  }
});

document.getElementById("story-edit").addEventListener("click", showStoryPlan);

document.getElementById("story-trash").addEventListener("click", async () => {
  const button = document.getElementById("story-trash");
  if (storyTrashArmed === null) {
    button.textContent = "Confirm trash";
    button.classList.add("armed");
    storyTrashArmed = setTimeout(() => {
      storyTrashArmed = null;
      button.textContent = "Trash summary";
      button.classList.remove("armed");
    }, CONFIRM_MS);
    return;
  }
  clearTimeout(storyTrashArmed);
  storyTrashArmed = null;
  button.textContent = "Trash summary";
  button.classList.remove("armed");
  try {
    await post("/api/story/trash", {});
    folderStory = null;
    storyBeats = [];
    storyVideo.removeAttribute("src");
    drawStoryEntry();
    closeStory();
  } catch (error) {
    storyResultNotice(error.message, true);
  }
});

// --- Opening and the list entry ---

function openStory(result) {
  storyDialog.hidden = false;
  if (result && folderStory && folderStory.file) {
    showStoryResult();
  } else {
    showStoryPlan();
  }
}

function closeStory() {
  storyVideo.pause();
  storyPeek.pause();
  storyPlaying = null;
  storyDialog.hidden = true;
}

document.getElementById("summarize").addEventListener("click", () => openStory(false));
document.getElementById("story-close").addEventListener("click", closeStory);
storyEntry.addEventListener("click", () => openStory(true));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !storyDialog.hidden) {
    closeStory();
  }
});

function drawStoryEntry() {
  const made = Boolean(folderStory && folderStory.file);
  storyEntry.hidden = !made;
  if (made) {
    const rating = folderStory.rating ? ` · ${folderStory.rating}` : "";
    storyEntry.querySelector(".name").textContent = folderStory.title || "Summary";
    storyEntry.querySelector(".span").textContent =
      `${fmtTenths(folderStory.duration)} · ${folderStory.beats.length} beats${rating}`;
  }
}

loaded.then(() => {
  folderStory = folderStoryAtLoad;
  drawStoryEntry();
});
