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
- `0006` — CLI test seam. Do not add an env/Settings switch for test
  doubles. Its private factories were replaced by the context object in 0013's
  item 4b amendment.
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
  saved transcript. Results are indicative, not statistical. A candidate hits
  a label at an overlap of half the shorter range (`HIT_FRACTION = 0.5`).
  Precision is one-to-one: in rank order each candidate claims the earliest
  unclaimed label it hits, and one on a claimed label is a repeat. Recall
  counts labels with any hit.
- `0013` — `--detector heuristic|llm` on `analyze` and `eval`, composed by
  `build_detector(settings, name)`. No test double is selectable by name.
  `AnalysisResult.detector` records the name (`llm:<model>` for the LLM),
  `None` in older JSON; `render` and `eval` don't check it.
  `CLIPPER_LLM_MODEL` defaults to `qwen3.5:9b` (0014); an empty value is an
  `Error:`, exit 1. Amends 0006. Item 4b amendment: `AppContext` in
  `cli/context.py`, a frozen dataclass of builders (`transcriber`,
  `detector`, `renderer`, `probe`), set as `ctx.obj` by the root callback.
  Tests pass `obj=AppContext(...)` to `CliRunner().invoke`; a new adapter is
  a new field.
- `0014` — the LLM detector re-ranks the heuristic's windows, via Ollama, and
  never falls back silently. Windowing and dedup were extracted first, as a
  behaviour-neutral change, into `adapters/detection/windows.py`
  (`sliding_windows`, `select_top`, `WINDOW_SECONDS`, `STEP_SECONDS`).
  Requests send `think: false`: with thinking, `qwen3.5:9b` took tens of
  seconds per window and ran out of context. The default model is
  `qwen3.5:9b`, chosen from manual steps L1 to L5; `heuristic` stays the
  default detector. Breaking score ties by the heuristic was measured and not
  adopted.
- `0015` — determinism is a property of the heuristic detector. Amends vision
  criterion 2.
- `0016` — `render --vertical`: 1080x1920, the whole frame fitted to the
  width and centred on a blurred copy of itself (amended: the original centre
  crop cut off too much and was replaced before release). One re-encode path;
  the vertical frame first, then captions. Off by default. Amends 0007 and
  0011.
- `0017` — word timestamps (`Segment.words`, default empty) and styled ASS
  captions from one pure builder that replaces `build_srt`. The transcriber's
  name gains `+words`, so old cache entries are re-transcribed. Segments
  without words become whole-segment cues, with a note. Supersedes 0010's
  segment-level decision; its burn-in mechanism stands. Item 4 amendment:
  `build_ass`, chunks of up to 3 words, one event per word with the active
  word yellow, 1080x1920 or 1920x1080 canvas, style chosen from frames.
- `0018` — `render --titles`: a `TitleWriter` Protocol and an Ollama adapter
  reusing `OllamaClient` write a title and hook per clip to the manifest. A
  title failure is that clip's failure; no fallback. Built through the context
  object that replaces the factories first. Item 5 amendment: the model sees
  only `clip_text` (what the captions show); the title is written before the
  clip is cut; no speech means a note and no title; one line, length not
  checked; `AppContext.title_writer`; manifest `titles`, `title`, `hook`.
- `0019` — clips go to `<clips dir>/<stem>/` with a `manifest.json` replaced
  each run. File names inside are unchanged. Amends 0008. Item 4a amendment:
  run-level flags, per-clip `file` or `error` and burned-in `captions`; no
  titles fields until item 5; a failure before cutting leaves the manifest.
- `0020` — `clipper fetch <url>`: one YouTube video or Twitch VOD per call,
  chosen by hand, via the yt-dlp program behind a `Downloader` Protocol.
  Both platforms' terms forbid downloading with a tool (quoted, read
  2026-11-03); the maintainer accepts the risk for personal use, and the
  README and `--help` say so. No playlists, channels, cookies or logins.
  Campaign sources unknown. Item 6 amendment: URL rules in
  `core/sources.py` before yt-dlp runs, `<videos dir>/<id>.mp4`
  (`CLIPPER_VIDEOS_DIR`, Twitch `v<number>`), `--ignore-config`, 1080p cap,
  no codec choice (YouTube gave AV1, which renders), an existing file reused.

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

## V3 scope (shipped as `v0.3.0`)

Input: saved `AnalysisResult` JSON and a hand-written labels file per video.
Output: a measured comparison of detectors, and a local-LLM detector that
re-ranks the heuristic's windows. Interface: `clipper eval`, and `--detector`
on `analyze` and `eval`.

Non-goals for V3: downloading or URL ingestion, posting, analytics or platform
metrics, a vector database or retrieval, a trained neural network, audio or
visual features, a labeling UI, a rename of the project, and any change to
`render`. Campaign briefs moved to "Later" (see the V4 list).

V3 closed with item 8. The Whisper experiment (item 5) moved to V4 as GPU
transcription, for speed: the labels are test examples, so `eval` cannot judge
a Whisper change on real work.

## V4 scope (next)

Input: a video URL (YouTube or Twitch) or a local file. Output: a folder per
video of post-ready drafts: 9:16 vertical (whole frame, blurred fill), styled
word-by-word captions burned in, and a suggested hook/title per clip. The
maintainer picks which to post, by hand. Interface: `clipper fetch`, changes
to `render`, and one command from a URL to that folder.

Non-goals for V4: posting or uploads, analytics or platform metrics, campaign
briefs, any change to which moments are picked (detectors, windows, boundaries),
audio or visual detection features, subject or face tracking for the crop,
hosted APIs or paid services, web UI, database, user accounts, ML training.

## Maintainer's goals

As stated: a personal tool, used daily and eventually mostly unattended, that
turns long-form videos into clips for niche short-form accounts, with the ML
side growing over time and costs kept near zero.

From the alignment interview on 2026-11-02:

- No accounts and no clipping yet. Target 4 to 10 long videos a week from
  Twitch VODs, YouTube streams, podcasts and Vyro/Whop campaigns. The niche is
  open.
- Finding moments is the expected time sink. Not yet measured.
- About half of clip-worthy moments are visual. Clip length should follow the
  moment, not a fixed window.
- Must-haves before daily use: 9:16 vertical, styled captions, hook/title
  text. "Unattended" means: give a URL, come back to rendered, edited clips in
  a folder, and choose which to post. Posting stays manual.
- Campaign briefs: none read yet, so whether they steer detection is open.
- Usefulness wins over ML depth when they conflict. For interviews: a working
  product whose ML choices are measured, built with ADRs, tests and CI.
- Strictly local and free; RTX 4070 Ti; 5 to 10 hours a week.
- The two label files were test examples, not a sample of real work.

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
- V3: `clipper eval`, label files, `--detector` and the LLM detector,
  released as `v0.3.0`. Notes are in `docs/releases/v0.3.0.md`.

In progress:

- V4. Items 1 (ADRs 0016 to 0019), 2 (`render --vertical`, #90, #92), 3
  (word timestamps, #93), 4 (styled captions, #95), 4a (a folder per video
  and the manifest, #97), 4b (the CLI context object, #99) and 5
  (`render --titles`, #101) and 6 (ADR 0020, #103; `clipper fetch`, #104)
  are merged. Next: item 7, GPU transcription.

V3 results, for reference:

- Item 7, the LLM detector: `--detector llm` via Ollama, with thinking off
  and `qwen3.5:9b` as the default model (ADR 0014 amendments). Its results on
  the two label files: `test_input.json` top 5, recall 4 of 4 and precision
  4 of 5 (1 repeat); `oFbD0Ot7nQ8.json` top 30, recall 6 of 30 and precision
  6 of 30 in 953 s, but 0 of 30 in its top 5. Both models tried cluster their
  scores on round values.
- Item 0 has two label files. With one-to-one precision (ADR 0012, item 6a
  amendment), the heuristic gives: on `test_input.json` (labels cover the
  whole speech) recall 3 of 4 and precision 3 of 5, with 2 repeats; on
  `oFbD0Ot7nQ8.json` recall 2 of 30 and precision 2 of 5 at top 5 (1 repeat),
  and recall 5 of 30 and precision 5 of 30 at top 30 (`CLIPPER_TOP_N=30`,
  2 repeats, both inside the 9.5-minute label 16). Precision is a lower bound
  on the long video. Before 6a, precision there was 3 of 5 and 7 of 30. Seven
  of its 30 labels are longer than two 45 s windows. Videos are downloaded by
  hand with `uv tool run yt-dlp` into `data/videos/`, named by video ID.
- The numbers above are on transcripts made without word timestamps. With them
  (V4 item 3, ADR 0017 amendment) segments are re-split, and the heuristic
  gives: `test_input.json` unchanged; `oFbD0Ot7nQ8.json` recall 1 of 30 at
  top 5 (precision 1 of 5) and 6 of 30 at top 30 (precision 6 of 30,
  2 repeats). The LLM detector has not been re-run on them.

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
   exists. Two label files so far: `test_input.mp4` (whole speech) and
   `oFbD0Ot7nQ8.mp4` (a 5:04:01 YouTube stream, 30 of the maintainer's main
   clips, not all clippable moments; ADR 0012 amendment). Both were test
   examples, not a sample of the videos the maintainer will process.
1. ~~ADRs 0012 to 0015 and the V3 scope (one docs PR)~~ (done)
2. ~~Label domain model, loader and `CLIPPER_LABELS_DIR`~~ (done)
3. ~~Pure comparison function and `clipper eval`, with the first label file
   format documented~~ (done)
4. ~~`--detector` option and the `_build_detector` seam, with the open question
   on recording the detector name in `AnalysisResult` (ADR 0013)~~ (done)
5. ~~Whisper experiment: a larger model and/or the GPU, measured with `eval`.
   Needs a manual-test step. May end in a settings change or in nothing.~~
   (moved to V4 item 7, as GPU transcription for speed)
6. ~~Extract windowing and overlap dedup from `HeuristicClipDetector`.
   Behaviour-neutral; the heuristic's tests stay unchanged (ADR 0014).~~
   (done; `adapters/detection/windows.py`)
6a. ~~Revisit the hit rule on the long-video labels. Half of the shorter range
   counts any 45 s candidate inside a long label as a hit (three of seven
   top-30 hits on `oFbD0Ot7nQ8.json`). A change is an ADR 0012 amendment,
   lands before item 7, and states its design and waits for confirmation.~~
   (done; the threshold stays at 0.5, precision is one-to-one in rank order)
7. ~~Local LLM detector via Ollama, a manual-test step, a repeatability check
   (ADRs 0014, 0015), and a model choice recorded from `eval` results~~
   (done; #79, #80, #81, #82 and the default-model PR; model `qwen3.5:9b`,
   ADR 0014 amendments)
8. ~~Release notes and tag `v0.3.0`, with items 1 to 4, 6, 6a and 7~~ (done;
   notes in `docs/releases/v0.3.0.md`, F1 run before tagging)

## V4 list

Each item is one PR, and each code item states its design and waits for
confirmation first. Output first: nothing here changes which moments are
picked.

1. ~~ADRs for the V4 decisions (one docs PR)~~ (done; 0016 vertical crop,
   0017 word timestamps and captions, 0018 titles, 0019 folder per video).
   The fetch ADR moved to item 6, since it depends on platform terms.
2. ~~9:16 centre crop in `render` (0016), with a manual-test step.~~ (done;
   #90, manual steps R15 to R17; the crop was then replaced by a fit over a
   blurred fill, ADR 0016 amendment)
3. ~~Word timestamps in the transcript (0017). A domain-model change. Whether
   faster-whisper's word timestamps are usable here, and what they cost, is
   unverified.~~ (done; #93, manual step W9; no measurable time cost, segments
   re-split, ADR 0017 amendment)
4. ~~Styled, word-by-word burned-in captions (0017), with a manual-test
   step.~~ (done; #95, manual steps C2 and C7 rewritten; ADR 0017 amendment)
4a. ~~A folder per video and the manifest (0019).~~ (done; #97, manual step
   R18; ADR 0019 amendment)
4b. ~~Replace the CLI's private factories with a context object.
   Behaviour-neutral; ADR 0013's trigger, since titles would be the fifth
   factory. A 0013 amendment.~~ (done; #99, `AppContext` in `cli/context.py`)
5. ~~A title and hook per clip, `render --titles` (0018), with a manual-test
   step.~~ (done; #101, manual step R19; ADR 0018 amendment)
6. ~~`clipper fetch <url>`: first an ADR (0020) on YouTube's and Twitch's
   terms, then the `yt-dlp` program as a subprocess behind a `Downloader`
   Protocol, saving to `data/videos/<id>.mp4`, with `analyze` unchanged.
   Manual-test step.~~ (done; ADR 0020 #103, `fetch` #104, manual steps D0 to
   D8; ADR 0020 amendment)
7. GPU transcription (was V3 item 5): time the long video on the GPU against
   the CPU's 20:59. May end in a settings change or in nothing. Manual-test
   step. No `eval` claim.
8. One command from a URL or file to a folder of drafts: fetch, analyze,
   render with `--vertical --captions --titles`. Its command shape needs an
   ADR or a 0011 amendment, decided in that item.
9. Release notes and tag `v0.4.0`, if the items above land.

Later, not scheduled:

- Picking, a candidate for V5: clip boundaries that follow the moment (ADR
  0014 "revisit when"); a local signal for visual moments, such as audio
  loudness (untried); and label files for the first niche chosen, so `eval`
  reflects real work.
- Campaign briefs as a detector input, once the maintainer has read some.
  Needs an ADR.
- Subject or face tracking for the vertical crop.
- Posting and performance data, once accounts exist.

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
  its whole sentence for the part inside the clip (ADR 0010). Since V4 item 4
  this applies only to segments without words (v0.3 analyses); word captions
  drop a word that starts before the clip. On the one 5:14 video tried, no cue
  in any rank was under 0.5 s, so it was not needed. Revisit if a real clip
  flashes a sentence, and set the threshold from that clip.
- YouTube's "Most replayed" graph as a second label source (ADR 0012). It is
  public on many long videos and may be extractable with yt-dlp. Both are
  unverified, and YouTube's terms have not been checked. A short spike would
  settle whether it is usable.
- Performance data from posted clips as a reference (ADR 0012). Needs posting
  and analytics, which are not planned yet. Clips have no stable ID today (they
  are named by time span), so joining metrics to clips needs one.
- A finer LLM score. Both models tried score on a few round values (on the
  long video, `llama3.1:8b` gave 0.8 to 403 of 1,215 windows), and neither
  put a label in its top 5 there. Options: token probabilities (whether
  Ollama returns them is unverified) or a scoring rubric in the prompt. Needs
  an ADR 0014 amendment and `eval` results.
- Labels may hinge on what is seen, not said. Several `oFbD0Ot7nQ8.json`
  labels name visual moments ("Biz Hits a Long Putt"), which a
  transcript-only detector cannot see. Unverified as the cause of the LLM's
  top-5 misses. Audio and visual features are a V3 non-goal.
- `OllamaClient` ignores `done_reason`. A reply cut off at the length limit
  shows as `not a valid score ... (got '')` instead of saying it was cut off.
  Not hit since `think: false` (ADR 0014).
- Letterboxed sources keep their bars under `--vertical` (ADR 0016, manual
  step R15): `test_input.mp4` has black bars in its 1280x720 frame, and they
  show inside the fitted picture and blurred in the fill. Fix: detect the
  picture area first (ffmpeg's `cropdetect`, untried here) and fit only that.
  An ADR 0016 amendment.
- `llama3.1:8b` worded 2 of 6 reasons differently across two runs at
  temperature 0 and seed 0 (ADR 0015 "revisit when"). Not the default model.
- `OllamaClient` lives in `adapters/detection/` but titles use it too, so
  `adapters/titles/` imports across adapters, and the client raises
  `DetectionError`, which the title writer re-raises as `TitleError`. Fix:
  move it to a shared adapter module with its own error, behaviour-neutral.
- Title quality (manual step R19, `qwen3.5:9b`): one of five titles was over
  the asked 60 characters, and rank 5's title and hook inverted the speaker's
  point. Judged by eye; revisit the prompt if real clips show it often.
- yt-dlp warns on every YouTube request that no JavaScript runtime was found
  and "some formats may be missing" (it asks for deno). Downloads worked in
  manual step D1. Revisit if YouTube downloads fail or lose formats (ADR 0020
  "revisit when").
