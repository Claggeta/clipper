# Clipper

Finds clip-worthy moments in long-form video. V1 takes a local video file,
transcribes it, and reports the top 5 clip-worthy timestamps.

## How to work in this repo

- Read the existing code before proposing changes. Match the current style.
- Keep each PR to one item from the M5 list. Say when something is out of
  scope rather than expanding the change silently.
- Before writing code for a design change, state the design and its tradeoffs,
  and wait for confirmation.
- Never commit to `main`. Work on a short-lived branch named
  `<type>/<short-description>`.
- Run the full check sequence before committing:
  `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
- Use Conventional Commits: `feat:`, `fix:`, `docs:`, `chore:`, `test:`, with a
  scope, e.g. `fix(media): report full ffmpeg stderr`.
- After a change, show the diff and the exact commands to verify it.
- Prefer small, reviewable changes over broad refactors.

## Architecture

src layout. Dependencies point inward:

- `domain/` — pure pydantic models, no I/O. The most stable layer.
- `core/` — Protocol interfaces, pipeline, settings. Depends on domain only.
- `adapters/` — ffmpeg, fake, faster-whisper, caching, video, heuristic
  detection. Depend on core and domain.
- `cli/` — Typer app and rich table. The composition root, and the only place
  that picks concrete adapter classes.

Tests mirror the module under test: `tests/unit/<layer>/test_<module>.py`, plus
`tests/integration/` for tests that use real third-party libraries.

## Decision records

Design decisions live in `docs/decisions/`, one ADR per decision, numbered.
Read the ADRs that touch a file before changing it. These constrain current
and planned code:

- `0003` — transcription dependency pins. Do not unpin `av` or
  `huggingface-hub` without checking both conditions in the ADR.
- `0004` — ffmpeg error reporting. `-hide_banner -loglevel error` is
  load-bearing for the error message being readable. Removing it silently
  degrades the message back toward ffmpeg's banner and metadata noise. Its
  "adapter stays agnostic" clause is amended by 0005. The PATH check, the
  full-stderr INFO log and the 10-line failure message live in
  `adapters/media/_process.py`; new ffmpeg-family adapters reuse them.
- `0005` — no-audio classification. The ffmpeg extractor matches
  `does not contain any stream` and raises `NoAudioStreamError`; anything else
  stays a generic `AudioExtractionError`. The command uses `-map 0:a:0?`, not
  `-map 0:a?`, which breaks the WAV muxer on multi-audio files.
- `0006` — CLI transcriber seam. `cli/main.py` builds the transcriber in
  `_build_transcriber(settings)`, which tests monkeypatch. Do not add an
  env/Settings switch for test doubles. Amended by 0011: `render` adds
  `_build_renderer` and `_build_probe` the same way.
- `0007` — clips are re-encoded (`libx264`, `aac`, `yuv420p`), one code path.
  No stream copy.
- `0008` — clips go to `data/clips/` (`CLIPPER_CLIPS_DIR`) as
  `<stem>_<HHMMSS>-<HHMMSS>.mp4`. Overwrite on collision, but fail within one
  run if two candidates get the same name.
- `0009` — `render` reads the saved `AnalysisResult` JSON, checks the source
  name, and uses a `MediaProbe` (`ffprobe`, JSON output) for duration and
  video-stream checks. No stderr matching for these.
- `0010` — captions are burned in from segment-level text via a pure SRT
  builder. Word-level captions are out of scope.
- `0011` — `render` is a separate command (`--rank`, `--captions`). If one clip
  fails, the rest still render, then it exits 1.
- `0012` — detectors are evaluated against hand-written label files (one JSON
  per video, `CLIPPER_LABELS_DIR`). `clipper eval` reruns a detector on the
  saved transcript. Results are indicative, not statistical.
- `0013` — `--detector heuristic|llm` on `analyze` and `eval`, composed in
  `_build_detector`. No test double is selectable by name. Amends 0006.
- `0014` — the LLM detector re-ranks the heuristic's windows, via Ollama, and
  never falls back silently. Windowing and dedup are extracted first, as a
  behaviour-neutral change.
- `0015` — determinism is a property of the heuristic detector. Amends vision
  criterion 2.

A design decision that isn't in an ADR is a decision that will be undone by
accident.

## Stack

Python 3.12, uv, pydantic v2, pydantic-settings, ruff, mypy (strict, with the
pydantic plugin), pytest + pytest-cov, pre-commit, GitHub Actions, Typer +
rich, faster-whisper, ffmpeg.

## V1 scope (shipped as `v0.1.0`)

Input a local video file, generate a transcript, identify the top 5
clip-worthy timestamps, and display the results. The interface is a CLI that
prints a table and saves JSON.

Non-goals for V1: clip rendering, captions, uploads, analytics, web UI,
database, user accounts, ML training.

## V2 scope (shipped as `v0.2.0`)

Input: the saved `AnalysisResult` JSON and the original video. Output: one
rendered video file per clip-worthy candidate, optionally with captions
burned in. Interface: a `clipper render` command.

Non-goals for V2: vertical reframing or cropping, caption styling beyond one
default, word-level captions, hardware encoders, concatenating clips, uploads,
analytics, web UI, database, user accounts, ML training, and any change to
the detector.

## V3 scope (in progress)

Input: saved `AnalysisResult` JSON and a hand-written labels file per video.
Output: a measured comparison of detectors, and a local-LLM detector that
re-ranks the heuristic's windows. Interface: `clipper eval`, and `--detector`
on `analyze` and `eval`.

Non-goals for V3: downloading or URL ingestion, posting, analytics or platform
metrics, a vector database or retrieval, a trained neural network, audio or
visual features, a labeling UI, a rename of the project, and any change to
`render`. Campaign briefs as a detector input come after the LLM detector
works and need their own ADR.

The maintainer's goal for the project, as stated: a personal tool, used daily
and eventually mostly unattended, that turns long-form videos into clips for
niche short-form accounts, with the ML side growing over time and costs kept
near zero. V3 serves that by making "better" measurable first.

## Current state

All merged to `main` with green CI:

- M0: repo, uv, tooling, pre-commit, CI, docs
- M1: domain models `Segment`, `Transcript`, `ClipCandidate`, `AnalysisResult`
- M2: `Transcriber` and `AudioExtractor` protocols; fake, faster-whisper,
  caching, and video adapters
- M3: heuristic clip detector with weighted signals (questions, exclamations,
  emphasis words, pace) over sliding windows, with overlap deduplication
- M4: `AnalysisPipeline`, `Settings`, and the `clipper analyze <video> [-v]`
  CLI, with table output and JSON written to `data/output/`
- M5: hardening, released as `v0.1.0` (pre-release). Notes are in
  `docs/releases/v0.1.0.md`.
- V2: clip rendering and captions, released as `v0.2.0`. Notes are in
  `docs/releases/v0.2.0.md`.

In progress:

- V3 (scope and ADRs 0012 to 0015 are merged; item 2, the label model, is
  merged). Next: item 3, the comparison function and `clipper eval`. Item 0 is
  started: one label file, `data/labels/test_input.json`, whose four labels
  cover the whole speech (ADR 0012, item 2 amendment).

## Pinned dependencies

See `docs/decisions/0003-pin-transcription-dependencies.md`.

- `huggingface-hub<1.0` — Hub 2.3.0 changed its API and dependency graph,
  pulling in httpx2 and httpcore2 in place of httpx.
- `av==17.0.0` — faster-whisper 1.2.1 calls
  `av.open(..., metadata_errors="ignore")`, which av 19.0.2 rejects. This was
  the actual cause of the first real-run failure.

Do not unpin these without checking both conditions in the ADR.

## First real run

SUCCESS on a 5:14 speech video, Whisper `base` model, CPU:

- 5 candidates produced, deterministic across runs
- Top score 0.26, bottom 0.03
- Cold run 14.3 s (model already cached; excludes the one-time download)
- Warm run, transcript cache hit: 0.38 s

## Known issues

1. CI stayed green while transcription was completely broken. Import-level
   checks and the fake transcriber never reach the real decode path. A smoke
   test now covers `decode_audio` (see `tests/integration/`).
2. Detector signals are weak on real Whisper output. The `base` model omits
   much punctuation, so `question_signal` and `exclamation_signal` rarely
   fire. Scores cluster low and `pace_signal` carries most of the weight.
   This is signal quality, not a bug, and it is the baseline future
   approaches are measured against.
3. The tests for the ffmpeg adapter mock `subprocess.run`, so nothing in CI
   confirms that `-loglevel error` leaves the cause line on stderr for a
   given ffmpeg build. `docs/manual-testing.md` (step F1) checks this by hand.
   Run it when ffmpeg, faster-whisper, av or huggingface-hub change, and before
   a release.

## M5 list

1. ~~Smoke test for the real decode path~~ (done)
2. ~~Fix the truncated ffmpeg error~~ (done)
3. ~~A clear error for a media file with no audio stream~~ (done)
4. ~~CLI success-path test with an injectable transcriber~~ (done)
5. ~~Update the README with real usage and sample output from the actual run~~
   (done)
6. Set the timing in `docs/vision.md`. (Done.)
7. ~~Tag `v0.1.0` with release notes~~ (done; notes in
   `docs/releases/v0.1.0.md`)
8. ~~A manual test checklist for Whisper and ffmpeg, since CI cannot run
   them~~ (done)

## V2 list

1. ~~ADRs 0007 to 0011 for the rendering decisions (one docs PR)~~ (done)
2. ~~Extract a shared ffmpeg helper from `FfmpegAudioExtractor` (stderr line
   cap, `which` check). Behaviour-neutral; this is the ADR 0004 trigger.~~
   (done)
3. ~~`ffprobe` adapter behind a `MediaProbe` Protocol (duration, has video)~~
   (done)
4. ~~Load a saved `AnalysisResult` from JSON, with the source-name check~~
   (done)
5. ~~`ClipRenderer` Protocol, `ClipRenderError`, `RenderedClip`, fake renderer,
   output naming~~ (done)
6. ~~`FfmpegClipRenderer`, no captions~~ (done)
7. ~~`clipper render` command with the `_build_renderer` seam~~ (done)
8. ~~Manual-test checklist: rendering, and a first real run~~ (done)
9. ~~Pure caption builder (transcript slice to SRT)~~ (done)
10. ~~Burn-in captions and a CLI flag~~ (done)
11. ~~Manual-test checklist: captions, README usage and sample output, release
    notes, tag `v0.2.0`~~ (done; notes in `docs/releases/v0.2.0.md`)

## V3 list

Each item is one PR, and each code item states its design and waits for
confirmation first (see "How to work in this repo").

0. Not a PR: the maintainer collects long videos (Twitch/YouTube VODs,
   podcasts, a mix of campaign and non-campaign), runs `analyze` on them, and
   writes label files. `eval` cannot say anything about real media until this
   exists. Started: labels for `test_input.mp4` only. No long videos yet.
1. ~~ADRs 0012 to 0015 and the V3 scope (one docs PR)~~ (done)
2. ~~Label domain model, loader and `CLIPPER_LABELS_DIR`~~ (done)
3. Pure comparison function and `clipper eval`, with the first label file
   format documented
4. `--detector` option and the `_build_detector` seam, with the open question
   on recording the detector name in `AnalysisResult` (ADR 0013)
5. Whisper experiment: a larger model and/or the GPU, measured with `eval`.
   Needs a manual-test step. May end in a settings change or in nothing.
6. Extract windowing and overlap dedup from `HeuristicClipDetector`.
   Behaviour-neutral; the heuristic's tests stay unchanged (ADR 0014).
7. Local LLM detector via Ollama, a manual-test step, a repeatability check
   (ADRs 0014, 0015), and a model choice recorded from `eval` results
8. Release notes and tag `v0.3.0`, if the items above land

Later, not scheduled: campaign briefs as a detector input (needs an ADR).

## Parked

- ffmpeg's exit code prints as an unsigned 32-bit value on Windows, e.g.
  `exit 3199971767` for invalid data. Fix: normalise negative `AVERROR`
  values to signed form before display, and map the few codes this tool
  actually produces (invalid data, no stream, permission denied) to a short
  name, falling back to the bare signed number. `-1094995529` alone is no
  friendlier than `3199971767`. Seen again in manual step R14: exit
  `4294967283` is -13 (`EACCES`), cause "Permission denied".
- The `render` message for a malformed analysis JSON repeats the prefix:
  `Invalid JSON in analysis result <path>: Invalid JSON: ...`. The second
  part is pydantic's own message (manual step R11). Drop one of the two.
- Docs drift in `docs/architecture.md`: omits `AudioExtractor` from core
  interfaces; says the CLI depends on core, but it imports adapters; promises
  CLI config flags that don't exist; says ffmpeg is checked at startup, when
  the check is lazy. The diagram and layer text also need code fences so
  GitHub renders them correctly.
- Possible mojibake in `docs/roadmap.md` and `docs/architecture.md`.
- `.gitignore` duplicate entries (`.venv`, `dist/`, `__pycache__/`).
- Opt-in model-load test: needs a marker, `--strict-markers`, and
  deselecting by default.
- Captions on a build without libass: every selected clip fails with the same
  ffmpeg message (ADR 0011), and no such build has been tried. Fix: check once,
  before cutting, that ffmpeg has the `subtitles` filter, and exit 1 with
  nothing written. How to ask ffmpeg is unverified. Amends ADR 0011's check
  order and ADR 0010.
- `render` has no `-v`, so the full ffmpeg stderr INFO log (ADR 0004) is not
  reachable, only the 10-line failure message. Fix: a `--verbose` flag like
  `analyze`'s, and an amendment to ADR 0011.
- Minimum overlap for caption cues: a segment that crosses a clip's edge shows
  its whole sentence for the part inside the clip (ADR 0010). On the one 5:14
  video tried, no cue in any rank was under 0.5 s, so it was not needed. Revisit
  if a real clip flashes a sentence, and set the threshold from that clip.
- YouTube's "Most replayed" graph as a second label source (ADR 0012). It is
  public on many long videos and may be extractable with yt-dlp. Both are
  unverified, and YouTube's terms have not been checked. A short spike would
  settle whether it is usable.
- Performance data from posted clips as a reference (ADR 0012). Needs posting
  and analytics, which are not planned yet. Clips have no stable ID today (they
  are named by time span), so joining metrics to clips needs one.
