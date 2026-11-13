// Clip audio (ADR 0007, audio amendment; V7 item 9i): the Audio tab (clip
// volume, a music bed and sound effects from the maintainer's own files),
// effect markers on the timeline, and playback in the live preview through
// the browser's audio engine. The preview's ducking dips the music during
// the transcript's words; the burn-in ducks on the real sound. Loaded after
// app.js, precision.js and frame.js, before preview.js.
"use strict";

const audioBox = document.getElementById("audio-editor");
const audioNote = document.getElementById("audio-note");
const audioLane = document.getElementById("audio-lane");
const musicPlayer = document.getElementById("music-player");
// key -> {gain_db, music: {file, level_db, duck} | null, effects: [{file, at_seconds, level_db}]}
const audioEdits = new Map();
let audioFiles = { music: [], sfx: [] };
// The renderer's dip under speech, measured on a real clip (ffmpeg.py).
const DUCK_DB = -11.6;
const MUSIC_FADE = 1.0;
const MUSIC_LEVELS = [-30, -27, -24, -21, -18, -15, -12, -9, -6, -3, 0];
const EFFECT_LEVELS = [-20, -16, -12, -8, -4, 0, 2, 4, 6];
let audioGraph = null; // {context, clipGain, musicGain}, made on first play
let lastAudioTime = null; // the source time of the last preview frame

function decibels(db) {
  return Math.pow(10, db / 20);
}

function savedAudio(clip) {
  const judged = clip.judgment && clip.judgment.start !== undefined;
  return (judged ? clip.judgment.audio : clip.audio) || null;
}

function audioOf(clip) {
  if (!audioEdits.has(clip.key)) {
    const saved = savedAudio(clip) || {};
    audioEdits.set(clip.key, {
      gain_db: saved.gain_db || 0,
      music: saved.music ? { ...saved.music } : null,
      effects: (saved.effects || []).map((effect) => ({ ...effect })),
    });
  }
  return audioEdits.get(clip.key);
}

// The audio Save sends; null when it changes nothing.
function sentAudio(clip) {
  const audio = audioOf(clip);
  if (!audio.gain_db && !audio.music && !audio.effects.length) {
    return null;
  }
  return {
    gain_db: audio.gain_db,
    music: audio.music,
    effects: [...audio.effects]
      .sort((a, b) => a.at_seconds - b.at_seconds)
      .map((e) => ({ file: e.file, at_seconds: round(e.at_seconds), level_db: e.level_db })),
  };
}

function audioText(audio) {
  if (!audio) {
    return "null";
  }
  return JSON.stringify([
    audio.gain_db || 0,
    audio.music ? [audio.music.file, audio.music.level_db, Boolean(audio.music.duck)] : null,
    [...(audio.effects || [])]
      .map((e) => [e.file, round(e.at_seconds), e.level_db])
      .sort((a, b) => a[1] - b[1]),
  ]);
}

function sameAudio(clip) {
  return audioText(sentAudio(clip)) === audioText(savedAudio(clip));
}

function audioChanged() {
  drawUnsaved();
  drawAudioLane();
}

// --- The tab ---

function labelled(text, control) {
  const label = document.createElement("label");
  label.className = "audio-field";
  label.append(text, control);
  return label;
}

function drawAudioEditor(clip) {
  const audio = audioOf(clip);
  audioBox.replaceChildren();

  const gain = document.createElement("input");
  gain.type = "range";
  gain.min = "-12";
  gain.max = "12";
  gain.step = "1";
  gain.value = String(audio.gain_db);
  const gainText = document.createElement("span");
  gainText.className = "muted";
  const showGain = () => {
    gainText.textContent = `${audio.gain_db > 0 ? "+" : ""}${audio.gain_db} dB`;
  };
  showGain();
  gain.addEventListener("input", () => {
    audio.gain_db = Number(gain.value);
    showGain();
    audioChanged();
  });
  const gainRow = document.createElement("div");
  gainRow.className = "audio-row";
  gainRow.append(gain, gainText);
  audioBox.append(labelled("Clip volume", gainRow));

  const music = selectInput(
    [["", "No music"], ...audioFiles.music.map((name) => [name, name])],
    audio.music ? audio.music.file : "",
    (value) => {
      audio.music = value ? { file: value, level_db: -18, duck: true } : null;
      drawAudioEditor(clip);
      audioChanged();
    },
  );
  audioBox.append(labelled("Music", music));
  if (audio.music) {
    const row = document.createElement("div");
    row.className = "audio-row";
    const level = selectInput(
      MUSIC_LEVELS.map((db) => [String(db), `${db} dB`]),
      String(audio.music.level_db),
      (value) => {
        audio.music.level_db = Number(value);
        audioChanged();
      },
    );
    level.title = "Music level";
    const duck = document.createElement("label");
    duck.className = "fade";
    const duckBox = document.createElement("input");
    duckBox.type = "checkbox";
    duckBox.checked = Boolean(audio.music.duck);
    duckBox.addEventListener("change", () => {
      audio.music.duck = duckBox.checked;
      audioChanged();
    });
    duck.append(duckBox, " Lower under speech");
    row.append(level, duck);
    audioBox.append(row);
  }

  const effects = document.createElement("div");
  effects.className = "ranges";
  audio.effects.forEach((effect, index) => {
    const row = document.createElement("div");
    row.className = "effect-row";
    const file = selectInput(
      audioFiles.sfx.map((name) => [name, name]),
      effect.file,
      (value) => {
        effect.file = value;
        audioChanged();
      },
    );
    const at = numberInput(round(effect.at_seconds - span.start), (relative) => {
      effect.at_seconds = span.start + relative;
      audioChanged();
    });
    at.title = "At (s into the span)";
    const level = selectInput(
      EFFECT_LEVELS.map((db) => [String(db), `${db} dB`]),
      String(effect.level_db),
      (value) => {
        effect.level_db = Number(value);
        audioChanged();
      },
    );
    row.append(
      file,
      at,
      level,
      removeButton(() => {
        audio.effects.splice(index, 1);
        drawAudioEditor(clip);
        audioChanged();
      }),
    );
    effects.append(row);
  });
  audioBox.append(labelled("Sound effects (seconds into the span)", effects));
  const add = document.createElement("button");
  add.type = "button";
  add.textContent = "Add effect at playhead";
  add.disabled = !audioFiles.sfx.length || audio.effects.length >= 10;
  add.addEventListener("click", () => {
    audio.effects.push({
      file: audioFiles.sfx[0],
      at_seconds: round(clamp(player.currentTime, span.start, span.end - 0.1)),
      level_db: 0,
    });
    drawAudioEditor(clip);
    audioChanged();
  });
  audioBox.append(add);
  audioNote.textContent =
    audioFiles.music.length || audioFiles.sfx.length
      ? "Your own files, from data/music and data/sfx."
      : "Put music in data/music and sound effects in data/sfx (files you may post), then reload.";
  drawAudioLane();
}

// --- Effect markers on the timeline ---

function drawAudioLane() {
  audioLane.replaceChildren();
  if (!current || !span) {
    audioLane.hidden = true;
    return;
  }
  const effects = audioOf(current).effects;
  audioLane.hidden = effects.length === 0;
  for (const effect of effects) {
    const mark = document.createElement("div");
    mark.className = "effect-mark";
    mark.style.left = `${percent(effect.at_seconds)}%`;
    mark.title = `${effect.file} at ${fmtTenths(effect.at_seconds)}`;
    mark.addEventListener("pointerdown", (event) => {
      event.stopPropagation();
      player.currentTime = effect.at_seconds;
    });
    mark.addEventListener("click", (event) => event.stopPropagation());
    audioLane.append(mark);
  }
}

// --- Preview playback ---

// The browser's audio engine, made on the first preview play (it needs a
// press); the source player then always plays through `clipGain`.
function ensureAudioGraph() {
  if (audioGraph) {
    return audioGraph;
  }
  try {
    const context = new AudioContext();
    const clipGain = context.createGain();
    context.createMediaElementSource(player).connect(clipGain).connect(context.destination);
    const musicGain = context.createGain();
    context.createMediaElementSource(musicPlayer).connect(musicGain).connect(context.destination);
    audioGraph = { context, clipGain, musicGain };
  } catch {
    audioGraph = null;
  }
  return audioGraph;
}

function speaking(seconds) {
  return words.some(([start, end]) => seconds >= start && seconds <= end);
}

// Called every preview frame: the clip's volume, the music in step with
// the finished clip's time, and effects as the playhead passes them.
function syncPreviewAudio() {
  const graph = audioGraph;
  const seconds = player.currentTime;
  const inSpan = current && span && seconds >= span.start && seconds < span.end;
  const playing = !player.paused && shownScreen() === "preview" && inSpan;
  const audio = current ? audioOf(current) : null;
  if (graph) {
    graph.clipGain.gain.value =
      shownScreen() === "preview" && audio ? decibels(audio.gain_db) : 1;
  }
  const music = playing && audio ? audio.music : null;
  if (music && graph) {
    const src = `/music/${encodeURIComponent(music.file)}`;
    if (musicPlayer.getAttribute("src") !== src) {
      musicPlayer.setAttribute("src", src);
    }
    const at = clipSeconds(seconds);
    const length = musicPlayer.duration || 0;
    const target = length ? at % length : at;
    if (Math.abs(musicPlayer.currentTime - target) > 0.3) {
      musicPlayer.currentTime = target;
    }
    if (musicPlayer.paused) {
      musicPlayer.play().catch(() => {});
    }
    const fade = clamp((clipLength() - at) / MUSIC_FADE, 0, 1);
    const duck = music.duck && speaking(seconds) ? decibels(DUCK_DB) : 1;
    graph.musicGain.gain.setTargetAtTime(
      decibels(music.level_db) * duck * fade,
      graph.context.currentTime,
      0.05,
    );
  } else if (!musicPlayer.paused) {
    musicPlayer.pause();
  }
  if (playing && audio && lastAudioTime !== null && seconds > lastAudioTime) {
    for (const effect of audio.effects) {
      if (lastAudioTime < effect.at_seconds && effect.at_seconds <= seconds) {
        const sound = new Audio(`/sfx/${encodeURIComponent(effect.file)}`);
        sound.volume = Math.min(decibels(effect.level_db), 1);
        sound.play().catch(() => {});
      }
    }
  }
  lastAudioTime = playing ? seconds : null;
}

// Made and resumed on a real press (a pointer or a key), which the browser
// needs: routed through a context that never starts, the player is silent.
function wakeAudio() {
  const graph = ensureAudioGraph();
  if (graph && graph.context.state === "suspended") {
    graph.context.resume().catch(() => {});
  }
}

document.addEventListener("pointerdown", wakeAudio, true);
document.addEventListener("keydown", wakeAudio, true);
// Outside the preview's frames too, so the music stops with the player.
player.addEventListener("timeupdate", syncPreviewAudio);
player.addEventListener("pause", syncPreviewAudio);

// Called by app.js when a clip is chosen, saved or reset.
function resetAudioEditor(clip, clear = false) {
  if (clear) {
    audioEdits.set(clip.key, { gain_db: 0, music: null, effects: [] });
  }
  drawAudioEditor(clip);
}

async function loadAudioFiles() {
  try {
    const response = await fetch("/api/audio");
    if (response.ok) {
      audioFiles = await response.json();
    }
  } catch {
    // No list: the tab offers nothing to pick.
  }
  if (current) {
    drawAudioEditor(current);
  }
}

loaded.then(loadAudioFiles);
