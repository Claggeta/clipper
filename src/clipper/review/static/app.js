// The review page (ADR 0024). Item 3a: list the clips and play the source
// video around the chosen one; trimming and saving come in later items.
"use strict";

const player = document.getElementById("player");
const list = document.getElementById("clips");
let current = null;

function fmt(seconds) {
  const whole = Math.floor(seconds);
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const s = whole % 60;
  return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function text(id, value) {
  document.getElementById(id).textContent = value;
}

function select(clip, button) {
  current = clip;
  for (const other of list.querySelectorAll("button")) {
    other.setAttribute("aria-current", String(other === button));
  }
  document.getElementById("empty").hidden = true;
  document.getElementById("details").hidden = false;
  text("title", clip.title || `Clip ${clip.rank}`);
  text("hook", clip.hook || "");
  text("clip-span", `${fmt(clip.start)} - ${fmt(clip.end)}`);
  text("clip-length", `${(clip.end - clip.start).toFixed(1)} s`);
  text("range-span", `${fmt(clip.range_start)} - ${fmt(clip.range_end)}`);
  const error = document.getElementById("error");
  error.hidden = !clip.error;
  error.textContent = clip.error ? `Render failed: ${clip.error}` : "";
  player.currentTime = clip.start;
}

// Stop at the end of the chosen clip's range.
player.addEventListener("timeupdate", () => {
  if (current && !player.paused && player.currentTime >= current.range_end) {
    player.pause();
    player.currentTime = current.range_end;
  }
});

async function load() {
  const response = await fetch("/api/clips");
  const data = await response.json();
  text("source", data.source);
  const flags = document.getElementById("flags");
  for (const name of ["vertical", "captions", "titles"]) {
    if (data[name]) {
      const flag = document.createElement("span");
      flag.textContent = name;
      flags.append(flag);
    }
  }
  for (const clip of data.clips) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    const rank = document.createElement("span");
    rank.className = "rank";
    rank.textContent = clip.rank;
    const name = document.createElement("span");
    name.className = clip.error ? "name failed" : "name";
    name.textContent = clip.title || (clip.error ? "Render failed" : `Clip ${clip.rank}`);
    const span = document.createElement("span");
    span.className = "span";
    span.textContent = `${fmt(clip.start)} - ${fmt(clip.end)}`;
    button.append(rank, name, span);
    button.addEventListener("click", () => select(clip, button));
    item.append(button);
    list.append(item);
  }
}

load();
