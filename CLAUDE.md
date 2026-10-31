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

## V2 scope

Input: the saved `AnalysisResult` JSON and the original video. Output: one
rendered video file per clip-worthy candidate, optionally with captions
burned in. Interface: a `clipper render` command.

Non-goals for V2: vertical reframing or cropping, caption styling beyond one
default, word-level captions, hardware encoders, concatenating clips, uploads,
analytics, web UI, database, user accounts, ML training, and any change to
the detector.

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

In progress:

- V2: clip rendering and captions. See the V2 list below.

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
9. Pure caption builder (transcript slice to SRT)
10. Burn-in captions and a CLI flag
11. Manual-test checklist: captions, README usage and sample output, release
    notes, tag `v0.2.0`

## Parked

- ffmpeg's exit code prints as an unsigned 32-bit value on Windows, e.g.
  `exit 3199971767` for invalid data. Fix: normalise negative `AVERROR`
  values to signed form before display, and map the few codes this tool
  actually produces (invalid data, no stream, permission denied) to a short
  name, falling back to the bare signed number. `-1094995529` alone is no
  friendlier than `3199971767`.
- Docs drift in `docs/architecture.md`: omits `AudioExtractor` from core
  interfaces; says the CLI depends on core, but it imports adapters; promises
  CLI config flags that don't exist; says ffmpeg is checked at startup, when
  the check is lazy. The diagram and layer text also need code fences so
  GitHub renders them correctly.
- Possible mojibake in `docs/roadmap.md` and `docs/architecture.md`.
- `.gitignore` duplicate entries (`.venv`, `dist/`, `__pycache__/`).
- Opt-in model-load test: needs a marker, `--strict-markers`, and
  deselecting by default.
