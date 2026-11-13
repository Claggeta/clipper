// Keys for the review page (ADR 0031, V7 item 9c). Loaded after app.js,
// whose functions and state it uses. Keys do nothing while typing in a
// box; Esc leaves the box.
"use strict";

// One frame, as the page does not know the video's frame rate.
const FRAME_SECONDS = 1 / 30;
const MOVE_ON_KEY = "clipper-review-move-on";
const moveOnBox = document.getElementById("move-on");
const keysHelp = document.getElementById("keys-help");

function typingIn(target) {
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement ||
    (target instanceof HTMLElement && target.isContentEditable)
  );
}

// The player on screen: the draft when it is shown, else the source.
function shownPlayer() {
  return screen === "draft" && hasDraft ? draft : player;
}

function playOrPause() {
  if (shownScreen() === "preview") {
    togglePreviewPlay();
    return;
  }
  const video = shownPlayer();
  if (video.paused) {
    video.play().catch(() => {});
  } else {
    video.pause();
  }
}

function stepBy(seconds) {
  const video = shownPlayer();
  const end = Number.isFinite(video.duration) ? video.duration : Infinity;
  video.currentTime = clamp(video.currentTime + seconds, 0, end);
}

function stepFrame(direction) {
  shownPlayer().pause();
  stepBy(direction * FRAME_SECONDS);
}

function press(id) {
  document.getElementById(id).click();
}

function rate(value) {
  ratingBox.querySelector(`[data-value="${value}"]`).click();
}

// Through Preview, Source and Draft, skipping the draft when there is none.
function toggleScreen() {
  const order = ["preview", "source", "draft"].filter(
    (name) => name !== "draft" || hasDraft,
  );
  showScreen(order[(order.indexOf(shownScreen()) + 1) % order.length]);
}

// The clips in list order.
function listedClips() {
  return [...list.querySelectorAll("button")]
    .map((button) => [...buttons].find(([, b]) => b === button))
    .filter(Boolean)
    .map(([key]) => clipsByKey.get(key));
}

function selectClip(clip) {
  select(clip, buttons.get(clip.key));
  buttons.get(clip.key).scrollIntoView({ block: "nearest" });
}

// The next or previous clip in the list; the first one if none is chosen.
function neighbour(direction) {
  const clips = listedClips();
  if (!clips.length) {
    return;
  }
  const at = clips.indexOf(current);
  const next = at < 0 ? 0 : clamp(at + direction, 0, clips.length - 1);
  selectClip(clips[next]);
}

// After a Save or Trash: the next clip with no verdict yet, after the one
// judged, wrapping round the list.
function moveOn(judgedClip, message) {
  const clips = listedClips();
  const at = clips.indexOf(judgedClip);
  const order = [...clips.slice(at + 1), ...clips.slice(0, Math.max(at, 0))];
  const next = order.find((clip) => badgeText(clip) === "");
  if (!next) {
    judgeNotice(`${message} Every clip has a verdict.`);
    return;
  }
  selectClip(next);
  judgeNotice(`${message} On to ${clipName(next)}.`);
}

function readMoveOn() {
  try {
    return localStorage.getItem(MOVE_ON_KEY) !== "off";
  } catch {
    return true;
  }
}

moveOnBox.checked = readMoveOn();
moveOnBox.addEventListener("change", () => {
  try {
    localStorage.setItem(MOVE_ON_KEY, moveOnBox.checked ? "on" : "off");
  } catch {
    // Storage is blocked; the choice lasts until the page reloads.
  }
});

document.addEventListener("judged", (event) => {
  const { clip, message } = event.detail;
  // Only when the clip judged is still the one shown (or was removed).
  if (moveOnBox.checked && (current === clip || current === null)) {
    moveOn(clip, message);
  }
});

function toggleHelp() {
  keysHelp.hidden = !keysHelp.hidden;
}

document.getElementById("keys-close").addEventListener("click", toggleHelp);
document.getElementById("keys-open").addEventListener("click", toggleHelp);

// Keys that need a clip chosen, by `event.key` (lower case, Shift apart).
const CLIP_KEYS = {
  " ": playOrPause,
  k: playOrPause,
  j: (shift) => stepBy(shift ? -1 : -5),
  l: (shift) => stepBy(shift ? 1 : 5),
  ",": () => stepFrame(-1),
  ".": () => stepFrame(1),
  i: () => press("set-start"),
  o: () => press("set-end"),
  p: () => press("preview"),
  "[": markCutIn,
  "]": markCutOut,
  1: () => rate("good"),
  2: () => rate("ok"),
  s: () => press("save"),
  delete: () => press("trash"),
  d: toggleScreen,
  t: () => press("add-text"),
};

// Keys that work with no clip chosen.
const PAGE_KEYS = {
  arrowdown: () => neighbour(1),
  arrowup: () => neighbour(-1),
  n: (shift) => neighbour(shift ? -1 : 1),
  a: () => press("add"),
  "?": toggleHelp,
};

document.addEventListener("keydown", (event) => {
  // The summary dialog has its own Esc; the page's keys wait (ADR 0032).
  if (!document.getElementById("summary-dialog").hidden) {
    return;
  }
  const key = event.key.toLowerCase();
  if (typingIn(event.target)) {
    if (key === "escape") {
      event.target.blur();
    }
    return;
  }
  if ((event.ctrlKey || event.metaKey) && key === "s" && !event.altKey) {
    event.preventDefault();
    if (current) {
      press("save");
    }
    return;
  }
  if (event.ctrlKey || event.metaKey || event.altKey) {
    return;
  }
  if (key === "escape") {
    if (!keysHelp.hidden) {
      toggleHelp();
    } else if (current) {
      dropCutIn();
    }
    return;
  }
  const action = PAGE_KEYS[key] || (current ? CLIP_KEYS[key] : undefined);
  if (action) {
    // Also keeps Space from pressing a focused button, and a focused video
    // from handling the key a second time.
    event.preventDefault();
    action(event.shiftKey);
  }
});
