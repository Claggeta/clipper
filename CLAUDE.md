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
  No stream copy. Audio amendment (V7 9i): clip volume, a looped music
  bed ducked by `sidechaincompress` (threshold 0.01, ratio 10: -11.6 dB
  under speech) and sound effects by `adelay`, mixed in the same encode
  from the maintainer's own files; needs the source's audio stream.
- `0008` — clips go to `data/clips/` (`CLIPPER_CLIPS_DIR`) as
  `<stem>_<HHMMSS>-<HHMMSS>.mp4`. Overwrite on collision, but fail within one
  run if two candidates get the same name.
- `0009` — `render` reads the saved `AnalysisResult` JSON, checks the source
  name, and uses a `MediaProbe` (`ffprobe`, JSON output) for duration and
  video-stream checks. No stderr matching for these. Start offset
  amendment: the probe also reads the file's start time, for `review`.
  Face style amendment: and the video's width and height (0025).
- `0010` — captions are burned in from segment-level text via a pure SRT
  builder. Word-level captions are out of scope.
- `0011` — `render` is a separate command (`--rank`, `--captions`). If one clip
  fails, the rest still render, then it exits 1. Review amendment (V5 3d):
  the review page's Save is a second caller of the same render path; a
  failure leaves the old file in place.
- `0012` — detectors are evaluated against hand-written label files (one JSON
  per video, `CLIPPER_LABELS_DIR`). `clipper eval` reruns a detector on the
  saved transcript. Results are indicative, not statistical. A candidate hits
  a label at an overlap of half the shorter range (`HIT_FRACTION = 0.5`).
  Precision is one-to-one: in rank order each candidate claims the earliest
  unclaimed label it hits, and one on a claimed label is a repeat. Recall
  counts labels with any hit. Twitch clips amendment: `clipper labels <links
  file>` writes `<labels dir>/v<id>.json` from hand-collected clip links and
  their "Watch full video" links; a clip spans `t - length` to `t` (`t` up to
  4 s before the real end, on 4 clips), overlaps merge, `--force` to
  overwrite. Precision against clips is a lower bound. V5 amendment: judged
  drafts are the main reference, in `data/labels/picks/` apart from clip
  labels: `post`, `fix` (with the maintainer's span) or a rejected span,
  pooled over every variant's top 10. Item 2 amendment: optional `verdict`
  (`post`, `fix` with a required `draft` span, `added`) and `rejected`
  spans in the same file, all labels judged or none; a candidate is on a
  label, else rejected, else unjudged; `eval` reports the gap (label minus
  candidate) at each end instead of seconds dragged, and a keeper ends
  early or starts late past `CUT_SHORT_SECONDS = 1.0`. Draft span
  amendment: a candidate also hits a `fix` label whose `draft` is its exact
  span, however far the fix moved it. Overlap amendment (V7 2): judged
  labels may overlap, ordered by start; clip labels may not.
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
  adopted. Detector examples amendment (V6 4, not adopted): 4 kept and 4
  rejected excerpts from other videos in the system prompt; on four VODs
  at top 10, keepers 29 to 25 (27), rejects 11 to 12; the examples
  reshuffled the top 10 rather than sharpening it.
  Shared client amendment (V7 4): `OllamaClient` and `TextGenerator` in
  `adapters/llm/ollama.py`, raising `LlmError`, which the detector and
  title writer turn into their own errors with the message kept.
  Drafts defaults amendment (V5 11): `drafts` uses the LLM and `--top N`
  (default 10, `CLIPPER_TOP_N` not used); `analyze` and `eval` keep the
  heuristic and top 5. Judged keepers at top 10 on three VODs: LLM 18,
  heuristic 12, half or more in ranks 6 to 10; LLM ends early more often.
- `0015` — determinism is a property of the heuristic detector. Amends vision
  criterion 2.
- `0016` — `render --vertical`: 1080x1920, the whole frame fitted to the
  width and centred on a blurred copy of itself (amended: the original centre
  crop cut off too much and was replaced before release). One re-encode path;
  the vertical frame first, then captions. Off by default. Amends 0007 and
  0011. Zoom amendment (V7 9h): punch-ins over source-time stretches,
  1.1 to 2.0x, a centre, cut-in or smooth, by per-frame `scale` and a
  `crop` whose offsets are expressions of `t`; vertical only.
- `0017` — word timestamps (`Segment.words`, default empty) and styled ASS
  captions from one pure builder that replaces `build_srt`. The transcriber's
  name gains `+words`, so old cache entries are re-transcribed. Segments
  without words become whole-segment cues, with a note. Supersedes 0010's
  segment-level decision; its burn-in mechanism stands. Item 4 amendment:
  `build_ass`, chunks of up to 3 words, one event per word with the active
  word yellow, 1080x1920 or 1920x1080 canvas, style chosen from frames.
  Caption editing amendment (V7 9f): `CaptionEdits` on the manifest clip
  and judged label, word fixes and hidden words by start time, key words,
  a highlight colour of five, applied before chunking.
- `0018` — `render --titles`: a `TitleWriter` Protocol and an Ollama adapter
  reusing `OllamaClient` write a title and hook per clip to the manifest. A
  title failure is that clip's failure; no fallback. Built through the context
  object that replaces the factories first. Item 5 amendment: the model sees
  only `clip_text` (what the captions show); the title is written before the
  clip is cut; no speech means a note and no title; one line, length not
  checked; `AppContext.title_writer`; manifest `titles`, `title`, `hook`.
  Title prompt amendment (#114): limits in words (title <= 8, hook <= 15),
  keep the speaker's meaning, no emoji or swear words; on 35 clips no title
  over 60 or hook over 100 characters (7 and 6 before). One clip still
  swears under every prompt tried, and meaning errors remain; not handled in
  code. Review amendment (V5 3c): the review page may change or clear a
  title alone, so title and hook are no longer required together.
  Title context amendment (V5 8b): with `<stem>.info.json` beside the
  video, the prompt gets the channel and video title and is told to name the
  streamer; titles naming the person on 39 keepers: 1 before, 12 with video
  info, 24 with names written by hand (a separate item).
  `--about` amendment (V5 8c): `--about "<text>"` on `render` and
  `drafts` puts an `About:` line first, one line, up to 300 characters,
  titles on, not saved; 22 of 39 titles name the person.
  Pronouns amendment (V5 8d): no he/she unless `--about` states them
  ("Lacy (he/him)"), repeat the name instead; no check on the reply. On 85
  clips: Lacy as she/her 4 to 2 of 10 (0 with pronouns stated), any
  gendered pronoun 55 to 45, keepers' titles naming the person 14 to 23 of 30.
  Title examples amendment (V6 3, not adopted): up to 8 of the
  maintainer's rewrites from other videos in the prompt, same channel
  first; on 110 keepers titles naming the person 48 to 32 (titles alone
  38), the maintainer's person 28 to 18 of 65, swear words 1 to 4. The
  model copied tone and other videos' people, not naming. No code merged.
  Saved about amendment (V6 3a): `data/channels.txt`
  (`CLIPPER_CHANNELS_FILE`, committed), `<channel> | <about text>` per
  line, used when no `--about` is given and the video has an info file;
  checked before any download; prints `About (saved for <channel>)`. On
  the 110 keepers: naming the person 48 to 68, the maintainer's person 28
  to 36 of 65, wrong names 4 to 6 (13 before the CORE line said to name a
  member only when the transcript does).
  Emoji amendment (V6 3b, the maintainer's choice): titles end with one
  or two fitting emoji (110 of 110; hooks got none); naming the person 68
  to 61, the maintainer's person 36 to 34 of 65.
- `0019` — clips go to `<clips dir>/<stem>/` with a `manifest.json` replaced
  each run (since V6 4d `<clips dir>/<channel>/<MM-DD-YYYY>/<stem>/`, an
  existing folder reused). File names inside are unchanged. Amends 0008. Item 4a amendment:
  run-level flags, per-clip `file` or `error` and burned-in `captions`; no
  titles fields until item 5; a failure before cutting leaves the manifest.
  Review amendment (V5 3c): `reviewed` (`saved`/`trashed`) per clip; a
  trashed clip has no file; Save leaves start and end until a re-render.
  Re-render amendment (V5 3d): start, end and file follow the new span, and
  `draft_start_seconds`/`draft_end_seconds` keep the detector's; `added`
  marks a clip added in the page (V5 3e).
- `0020` — `clipper fetch <url>`: one YouTube video or Twitch VOD per call,
  chosen by hand, via the yt-dlp program behind a `Downloader` Protocol.
  Both platforms' terms forbid downloading with a tool (quoted, read
  2026-11-03); the maintainer accepts the risk for personal use, and the
  README and `--help` say so. No playlists, channels, cookies or logins.
  Campaign sources unknown. Item 6 amendment: URL rules in
  `core/sources.py` before yt-dlp runs, `<videos dir>/<id>.mp4`
  (`CLIPPER_VIDEOS_DIR`, Twitch `v<number>`), `--ignore-config`, 1080p cap,
  no codec choice (YouTube gave AV1, which renders), an existing file reused.
  Clip lookups amendment: `labels` reads clip metadata with yt-dlp
  (`--skip-download`), one request per hand-listed clip, under the same
  accepted risk; clips stay refused by `fetch`.
  Video info amendment (V5 8a): `fetch` saves
  `<videos dir>/<id>.info.json` (title, channel, upload date, duration,
  description) from one `--skip-download` request, for a reused file too;
  a failed lookup is a warning.
  Remux amendment (V6 10): `fetch` probes the start time of a new or
  reused file and, past 0.1 s, remuxes it in place (`-map 0 -c copy
  -movflags +faststart`, through a `.part.mp4`); manual step D13.
- `0021` — GPU transcription measured and not adopted: the 5:04:01 stream
  took 593 s on the GPU (`int8_float16`) against 1,260 s on the CPU, but its
  transcript drifted (87.4% word match, far fewer `.` and `?`), and the
  setup needs ~1 GB of NVIDIA cuBLAS/cuDNN packages plus Windows DLL-path
  code. Unquantised GPU types drop nearly all punctuation. Transcription
  stays CPU `int8`; revisit with a larger Whisper model (V5).
  `small` pilot amendment (V5 item 12): on one VOD, 1.8x slower per hour,
  half the periods, and the LLM's top 10 lost all 5 known keepers (9
  unjudged); stopped at one VOD, Whisper stays `base`.
- `0022` — `clipper drafts <source>`: a `http(s)://` source is fetched
  (0020), anything else is a file; then the same analyze and render code
  (`_analyze_video`, `_plan_render`, `_cut_clips`, `_fetch_video` in
  `cli/main.py`). Vertical, captions and titles on by default, each with a
  `--no-` switch; no `--rank`. A refused URL, missing file or empty
  `CLIPPER_LLM_MODEL` fails before any download or transcription; clip
  failures follow 0011/0018 and `Drafts: <folder>` still prints. List
  amendment (V5 9): several sources or `--list` (one per line, `#`
  comments, `| about` per line), all checked first, run in order, a
  failed source does not stop the rest, a summary table, exit 1 if any
  failed. Measured
  (criterion 5): the 5:04:01 stream cold from a file in 22:58.
  Defaults amendment (V5 11): LLM detector, top 10, `--top N`; an empty
  model fails before download with titles off too (0014).
- `0023` — candidate boundaries follow the moment: candidates 15 to 90 s;
  in shared code for every detector, windows are scored, neighbours merged,
  the top N selected, then each end extended to the next pause in speech,
  never into the next chosen candidate. Pause length and merge rule are set
  from the V5 baseline's dragged ends, not guessed. Starts stay. Each change
  is measured before and after on judged labels and not adopted if it does
  not help. Amends 0014 decision 2. Baseline amendment (V5 item 4): on
  three VODs 12 of 31 keepers end early, moved 2.4 to 36.7 s later (median
  17.1 s), and 3 start earlier; item 5 sets the pause length from those 12
  ends, and starts stay. Item 5 amendment: `extend_ends` moves an end to the
  next gap of `PAUSE_SECONDS = 1.5`, up to `MAX_CANDIDATE_SECONDS = 90.0`
  and never past a later chosen candidate's start; adopted for the
  heuristic only (early ends 6 to 1, ends over 5 s long 1 to 4), not the
  LLM (6 early either way, 1 to 7 over), so decision 2's shared steps are
  opt-in. Item 6 amendment: `sliding_windows` drops windows under
  `MIN_CANDIDATE_SECONDS = 15.0` for both detectors; the heuristic is
  unchanged, the LLM keeps 18 keepers and 12 rejects with no draft under
  15 s, losing one mostly silent keeper. Item 7 amendment: decision 4
  (merging) withdrawn; neighbours stay separate clips.
- `0024` — `clipper review <drafts folder>`: a local browser page on the
  loopback address, plain HTML/CSS/JS, no build step, dark theme. Clip list,
  a player on the source video, a film strip of the clip plus 120 s either
  side with start and end handles, title and notes boxes. Save writes a
  judged label and re-renders that clip with the folder's flags; Trash
  deletes the file and writes a rejected span; Add clip saves a new span
  from the current range. No URL box until the drafts chain moves to
  `core/` (0022). The page is checked by manual steps. Amends the web UI
  non-goal, 0011 and 0019. Item 3a amendment: standard-library
  `ThreadingHTTPServer` on 127.0.0.1 (`--port 8765`), `parse_range` for
  seeking, `SO_REUSEADDR` off on Windows (it let two servers share a port),
  a fixed list of paths, the video from `<videos dir>/<manifest source>` or
  `--video`, code in `src/clipper/review/` composed by `cli/` through
  `AppContext.serve`; manual steps RV0 to RV4. Whole-VOD amendment: the
  player and handles cover the whole video, not the clip plus 120 s (the
  pause at the range's end also broke Play), with -15 to +15 s step
  buttons; `range_start`/`range_end` stay as 3b's starting strip view.
  Item 3b amendment: a `FrameExtractor` Protocol and ffmpeg adapter (`-ss`
  before `-i`, 72 px JPEG), `/frames/<second>.jpg` (quarter seconds too
  since V7 9d) cached per video under
  `<cache dir>/frames/`; a strip at fixed 1 to 60 s steps with zoom and pan;
  handles dragged or set from the playhead; Preview span; manual steps RV5
  and RV6. Item 3c amendment: Save writes a `post` or `fix` label to
  `<labels dir>/picks/<stem>.json` and the title and `reviewed` to the
  manifest; Trash writes a rejected span, deletes the file, marks it
  trashed; a clip's own judgment is found by its draft span and replaced;
  a span over another clip's label is refused (409), as is Save on a
  trashed clip until 3d; POSTs need a JSON content type, the page's Origin
  and a loopback Host (403 otherwise); `core/judging.py`, a `ReviewFiles`
  Protocol, `review/store.py`; manual step RV7. Item 3d amendment: Save
  re-renders when the span differs from the file's or there is no file,
  through the same renderer with the folder's flags, to `<name>.part.mp4`
  then renamed, the old file deleted; the label is written first and
  stands if the render fails (`render_error`); the draft span is kept in
  the manifest; Save brings a trashed clip back; `review` loads the
  analysis for captions before serving; about 12 s per re-render; manual
  step RV8. Model title amendment: the page's first write keeps the
  model's title as `model_title`; Reset restores the draft's span and it.
  Item 3e amendment: "Add clip here" from the playhead; Save writes an
  `added` label (not `post`), renders it and appends a manifest entry
  (`added: true`, ranked last, score 0); Trash removes an added clip
  outright, with no rejected span; manual step RV9. Frame URL fix: frames
  are `/frames/<stem>-<size>/<second>.jpg`, since the browser cached
  frames by URL and showed one VOD's frames in another's strip. Start
  offset fix: `review` refuses a video whose first timestamp is past
  0.1 s and names the lossless remux; a Twitch VOD started at 64 s and the
  browser's player ran 64 s behind the transcript (its judged labels were
  moved by -64 s). Rating amendment (V6 4a): Save may carry `rating`
  (`good` to post, `ok` for the backlog) and `people` on screen, names from
  `data/people.txt` (`CLIPPER_PEOPLE_FILE`, committed); both on the judged
  label, optional; manual step RV11. Cuts amendment (0029): Cut silence,
  toggles, Preview over cuts, Save with `cuts`; manual step RV12.
  Overlap amendment (V7 2): Save and Add clip refuse only an overlap
  that would be a hit (half the shorter span); manual step RV15.
- `0025` — vertical styles: `fit` (0016, the default) and `face`, a
  full-height crop on the largest face, faces found once per clip by
  OpenCV YuNet (5 samples a second, cached); `drafts` renders both and the
  review page picks one per clip, recorded with the label. Largest face
  stands for the speaker; the facecam split is later. Spike: faces in 89
  to 99% of keeper frames on five videos; one subject dominates on 50%
  (IRL) to 98% (webcam). Amends 0016, 0019, 0024 and the V5 scope.
  Face tracking amendment (13b): `FaceTracker` in `core/`,
  `OpenCvFaceTracker` piping 1280x720 frames from ffmpeg, cached per
  span under `<cache dir>/faces/`; the model is kept in the package
  (MIT licence beside it), not downloaded. Face style amendment (13c):
  `render --vertical --style face`; `plan_face_crop` in `core/framing.py`;
  the renderer takes a
  `CropPath` and moves a named crop with `sendcmd`; a clip with no face
  is a note, not a failure; the probe reads width and height (0009).
  Locked framing amendment: one position per shot (cut jump 0.30),
  re-locked only after 2 s outside, holds of 2 s or more, close similar
  faces framed as a pair; 65 to 7 moves on a podcast clip, 52 to 3 on
  IRL. The listener-framed two-shot needs speaker detection (Later).
  Several styles amendment (13d): `drafts --style` (repeatable, default
  fit and face), the face file `<stem>.face.mp4` beside the fit one, one
  title per clip, manifest `styles` and per-clip `variants` (0019, 0022);
  a clip without a face still renders in fit. Hand crop amendment (V7
  9h): a 9:16 window centred by hand over a stretch, merged into the face
  path or laid over the fit with `overlay=enable`. Style switch amendment
  (13e): the review page plays the draft (`/clips/<file>`, listed names
  only) with a button per style; Save keeps one style, re-rendering in it
  if the span moved (face tracking in `review`), deletes the other, and
  records `style` on the judged label (0012, 0019, 0024).
- `0026` — learning: judged labels are the first training signal, YouTube
  performance only after a report on linked posts shows it adds something.
  In stages, each may end in nothing: the maintainer's titles and judged
  spans as LLM prompt examples, then a small trained ranker, then
  performance. Every learned change is measured per VOD with that VOD left
  out of training (V5's measures, counts per VOD) and adopted only if it
  beats the current default. Local and free: frozen pretrained encoders
  (text first, audio and frames later) with a small trained model on top,
  a simple classifier first and a small neural network once labels are
  enough; no fine-tuning, nothing trained from scratch; new dependencies
  through 0003. Embeddings of judged spans (and linked Shorts) are kept in
  a file for nearest-clip lookup (ranker input, prompt examples, why a
  pick); a local vector store only if a file stops being enough.
  Retraining is a command the maintainer runs; it prints held-out measures
  beside the current model's before replacing it. Amends the V5 non-goals.
  Ranker spike amendment (V6 5, not adopted): `nomic-embed-text` window
  embeddings and the heuristic's signals, logistic regression, leave one
  VOD out on 15 VODs (377 keeper, 204 reject windows): keeper over
  reject 0.59 to 0.61, per VOD 0.24 to 0.80; its top 10 141 to 145 of 150
  unjudged, so not scored; inside the LLM's top 10, 0.61 against the
  LLM's 0.56. Embedding 15 VODs about 1.5 min. Rerun when the labels
  about double.
- `0027` — performance data: a YouTube Studio CSV exported by hand and
  imported offline; no YouTube API, key, OAuth or login. The parser follows
  a real export, recorded in an amendment first (columns unverified);
  retention or swipe-away figures preferred over views. Shorts are linked
  to drafts by matching title, length and date, ambiguous ones confirmed by
  hand, confirmed links kept and reused. Each import a dated snapshot,
  compared by age and channel. Imported data stays out of git.
- `0028` — post queue: `clipper queue` lists saved clips rated `good`, not
  yet posted, one per drafts folder in turn (newest first), with a day each
  (`--per-day`, default 2, `--start`), and writes `<clips dir>/queue.md`
  (titles and captions in code blocks, campaigns, people); `--include-ok`.
  Campaigns from `data/campaigns.txt` (`<name> | <hashtag> | <minimum> |
  <people> [| <channels>]`), people only from review marks. `clipper posted
  <folder>/<file> <YouTube link>` records the post in `data/posts.json`
  (committed); a posted clip leaves the queue, and the link serves 0027.
  Channel folders amendment (0019): drafts go to
  `<clips dir>/<channel>/<MM-DD-YYYY>/<stem>/`; `posted` takes a full path
  or `<stem>/<file>`. Combined clips amendment (V7 9k-2): a rated
  combined clip is queued after its folder's clips with its clips'
  people, shown as `combined`; `posted` takes its file. Title amendment (V7 7g): `posted`
  also takes the title as the queue shows it, after trying a path; one
  match only, else an error listing matches or close titles. Relative paths amendment (V7 7f): a path relative
  to the current folder or the clips dir too, if it is a file under the
  clips dir; the record stays `<stem>/<file>`.
- `0029` — cuts: the review page proposes cutting pauses in speech (word
  gaps of `MIN_PAUSE_SECONDS = 0.6`, less `CUT_PADDING_SECONDS = 0.15` a
  side), each toggleable, Preview skipping them; Save stores `cuts`
  (source-time ranges inside the span) on the judged label and manifest,
  and the renderer joins the kept parts in the one re-encode (0007);
  captions and face-crop moves go through one time map. The span and
  `eval` are unchanged. Manual cuts later. Core amendment: `select`/
  `aselect` rather than `concat` (audio stays optional), `-t` on the input
  when cutting; `core/cuts.py`. Page amendment: `GET /api/pauses`, Save
  re-renders when the cuts change, manual step RV12. Manual cuts
  amendment (V7 3a): Cut in/out at the playhead (`[`, `]`), merged with
  cuts they meet, stored as ordinary cuts; Cut silence keeps manual and
  saved cuts; manual step RV16. Strip drag amendment (V7 3b): a drag
  over 4 px on the frames cuts the range, a click still seeks.
- `0030` — on-screen text and thumbnails, made in review: text boxes per
  clip (words, start and end in the finished clip's seconds, `top` or
  `middle`), bold white on a half-dark box, extra events in the clip's ASS
  (one encode, with or without word captions); the first starts as the
  title, 0 to 3 s at the top; stored on manifest and label, re-rendered on
  change. "Thumbnail" saves the playhead's frame in the clip's framing,
  without captions, with optional text, as `<clip stem>.thumb.jpg`.
  Rendering amendment: `OnScreenText`, events pinned with `\pos`, emoji
  monochrome. Page amendment: up to 5 rows, unsaved ones drawn over the
  draft, manual step RV13. Thumbnails amendment: `ClipRenderer.still`,
  `POST /api/clips/<rank>/thumbnail` in the style shown (fit when no face),
  `ManifestClip.thumbnail`, deleted by Trash, listed by the queue; RV14.
  Styling amendment (V7 9g): per text a colour of six, `box` or
  `outline` (a `TextOutline` style), large, medium or small, a 0.3 s fade
  and a free `x`/`y` dragged on the preview; defaults burn as before.
  One box amendment (V7 7e): `BorderStyle 4`, one box behind a wrapped
  text (per-line boxes overlapped darker), outline colour clear; needs
  libass 0.17 or newer.
- `0031` — the review page becomes a one-screen editor: clip list,
  a live 9:16 preview, an inspector with Clip, Captions, Text, Frame,
  Audio and Assist tabs, and a timeline of tracks (frames, waveform,
  cuts, texts, zooms, audio); mock in `docs/mockups/review-editor.html`.
  Plain HTML/CSS/JS, no build step or dependencies (0024). Keys for every
  frequent action; precision aids (waveform peaks, frame step, snapping
  to word edges); the live preview is close, Save's re-render exact. New
  edits (caption fixes and key words, text style and free position,
  zooms and hand crops, volume, music and sound effects from
  `data/music/` and `data/sfx/`) are stored on label and manifest and
  re-rendered in the one encode, each settled by its own amendment.
  An assistant (a chat box turning a request into the page's edits via
  the local LLM, applied unsaved) comes last, after a spike; no hosted
  model. Amends 0024; later items amend 0016, 0017, 0025, 0029, 0030.
  Layout amendment (9b): a CSS grid, `Source`/`Draft` switch until 9e,
  `Clip`, `Text` and `Frame` tabs, trim and cut buttons in the timeline
  bar, tab and hidden list in local storage, stacked below 1200 px;
  manual step RV17. Keys amendment (9c): `static/keys.js`, keys press
  the page's buttons, nothing while typing, move on to the next clip
  without a verdict after Save or Trash (a box turns it off); RV18.
  Precision amendment (9d): a `PeaksExtractor` (ffmpeg, 20 levels a
  second, cached under `<cache dir>/peaks/`, 118 s the first time on a
  4:39 VOD), `/api/words`, waveform and words lanes, snapping to word
  edges within 0.3 s (a toggle, Alt while dragging), 0.25 and 0.5 s a
  frame with quarter-second frames; `static/precision.js`; RV19.
  Live preview amendment (9e): Preview, Source and Draft views; a canvas
  drawn from the source player (fit with blurred fill, or the face crop
  from `/api/crop`), captions from `/api/captions` (`caption_chunks`,
  pulled out of `build_ass` unchanged; fonts at 0.87 for libass sizing),
  on-screen texts at clip time; `static/preview.js`; RV20.
  Caption editing amendment (9f): a Captions tab of word chips by chunk,
  fixes, hidden words, key words and a highlight colour (`CaptionEdits`,
  ADR 0017 amendment), previewed and burned; `static/captions.js`; RV21.
  Text styling amendment (9g): colour, box or outline, size, fade and a
  dragged position per text (ADR 0030 amendment); preview lines balanced
  as libass wraps them; RV22.
  Zoom and reframing amendment (9h): zooms and hand crops in the Frame
  tab, bars under the strip with draggable ends, drawn and panned on the
  preview (ADR 0016 and 0025 amendments); `static/frame.js`; RV23.
  Audio amendment (9i): an Audio tab of clip volume, music and sound
  effects from `data/music/` and `data/sfx/`, previewed through the
  browser's audio engine (ADR 0007 amendment); `static/audio.js`; RV24.
- `0032` — Combine (first called Summarize, renamed 2026-11-13): a VOD's
  saved clips joined in VOD order into `<stem>_combined.mp4` by
  `ClipRenderer.join` (`concat`, label cards as on-screen texts, music as
  a clip's), one more encode; its own `RenderManifest.combined` (parts,
  music, length, title, rating, thumbnail; an old `summary` key loads),
  no judged label; a dialog in `review` to make, title (Suggest from the
  local model), rate, thumbnail and trash it; past 180 s a warning;
  queued and posted like a clip. Manual steps RV25 and Q3.

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

## V4 scope (shipped as `v0.4.0`)

Input: a video URL (YouTube or Twitch) or a local file. Output: a folder per
video of post-ready drafts: 9:16 vertical (whole frame, blurred fill), styled
word-by-word captions burned in, and a suggested hook/title per clip. The
maintainer picks which to post, by hand. Interface: `clipper fetch`, changes
to `render`, and one command from a URL to that folder.

Non-goals for V4: posting or uploads, analytics or platform metrics, campaign
briefs, any change to which moments are picked (detectors, windows, boundaries),
audio or visual detection features, subject or face tracking for the crop,
hosted APIs or paid services, web UI, database, user accounts, ML training.

V4 closed with item 9. GPU transcription (item 7) was measured and not adopted
(ADR 0021). V5 was scoped on 2026-11-09 (below).

## V5 scope (shipped as `v0.5.0`)

Input: a video URL or local file, or a list of them, as in V4; and the
maintainer's judgments on drafts, made in a review page. Output: the same
folder of drafts, with candidates of 15 to 90 s that end where the moment
ends, titles that know the channel and the VOD title, and picking changes
measured against the maintainer's own verdicts. Interface: `clipper review
<drafts folder>`, a list of sources for `drafts`, and judged-label reporting
in `eval`.

Tuned and measured on streamer VODs, the first niche. Boundary and length
changes apply to every niche.

Non-goals for V5: posting or uploads, analytics or platform metrics, hosted
APIs or paid services, the movies/TV niche (needs a rights ADR first), tuning
for the education or podcast niches, a URL box in the review page, channel
watching or scheduling (ADR 0020: videos are chosen by hand), audience
signals as a detector input, the Twitch API, a trained model, a database,
user accounts. Face-aware cropping was a spike that ended in ADR 0025: a
`face` crop style and picking a style per clip are built in V5; the
facecam split comes later.

## V6 scope (shipped as `v0.6.0`)

Input: the maintainer's judged labels and edited titles, and later a
YouTube Studio CSV of the posted Shorts. Output: drafts picked and titled
by what Clipper has learned from them, each learned change measured on
VODs it did not learn from, and a report linking posted Shorts to their
drafts. Interface: changes to `drafts` and the title prompt, a retraining
command, and a CSV import (ADRs 0026 and 0027).

Non-goals for V6: posting or uploads, the YouTube or Twitch APIs, OAuth or
logins, hosted or paid models, training in the cloud, fine-tuning the LLM
or the encoders, a network trained from scratch, a vector database server,
audio or frame embeddings (after item 5, under "Later"), campaign payouts, the movies/TV niche,
channel watching or scheduling, a database, user accounts. The facecam
split and speaker detection stay under "Later".

V6 closed with item 11 on 2026-11-12. Daily-use editing in `review` (items
4a to 4e) and the remux landed; the learning items ended in nothing (3, 4,
5). The performance work (items 2 and 6 to 9) moved to a later version, at
the maintainer's choice; ADRs 0026 and 0027 still stand for it.

## V7 scope

Input: the same sources and drafts folders as V6, and the maintainer's
edits in `review`. Output: drafts picked by a finer LLM score if it
measures better, a `facecam` vertical style for streams with a facecam
box if a spike finds the box reliably, and a review page that saves
neighbouring keepers, takes cuts marked by hand, and (item 9, ADR 0031)
becomes a one-screen editor with a live preview, keys, precision aids,
caption, text, zoom and audio edits, and maybe a local-LLM assistant.
Interface: changes to `review`, the LLM prompt, `render`/`drafts`
styles, and small fixes from "Parked".

Every picking change is measured as in V5 and V6: per VOD at top 10
against the judged labels, keepers and rejects, keepers cut short and end
gaps, unjudged drafts judged first, against the current default.

Non-goals for V7: the performance work (CSV export, import, report,
performance as a target, retraining; ADRs 0026 and 0027 still stand,
after an export), rerunning the ranker spike (the judged labels have not
grown since it: 144 labels and 90 rejected spans on 17 VODs), speaker
detection, audio or frame embeddings, posting or uploads, the YouTube or
Twitch APIs, hosted or paid models, the movies/TV niche, a URL box in the
review page, channel watching or scheduling, a database, user accounts,
a JavaScript framework or build step for the page, downloaded or paid
music and sound effects.

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

From the V5 interview on 2026-11-09, after four trial VODs:

- The costliest failures are bad picks and clips that end too early. Titles
  and time per VOD matter less.
- Daily use: more drafts per VOD (top 10) that the maintainer filters,
  rather than a fixed keep rate out of 5.
- Niches: a few accounts are planned, for streamer clips, educational
  talks and speeches, and entertainment (podcasts, movies, TV). Streamers
  come first.
- 4 to 10 long videos a week. Overnight runs are fine, so time per VOD is not
  a constraint.
- Measured by both judged drafts (pooled labels) and keep rate per VOD.
  About a minute per new draft for judging; no full rewatch.
- Unattended: a queue of URLs is enough. No scheduling.
- Off the table: hosted or paid APIs, posting. Movies/TV is out of V5.
- Titles should name the people and the angle; take context from the
  video's metadata.
- A review GUI, wanted until the drafts rarely need editing: clip list,
  player, a trim strip of the clip plus 2 minutes either side, title and
  notes, save and delete, dark and clean.
- Face tracking is wanted, but its form (facecam split or following the
  person) is not decided.
- Clip length: 15 to 90 s.

From the V6 conversation on 2026-11-11:

- The goal is a self-learning system: the drafts kept, the titles written
  and, eventually, how posted Shorts do should improve the next drafts.
- The maintainer has been posting many Shorts to YouTube, all from
  `drafts`, with no record of which draft became which Short. How many is
  not recorded.
- Performance data comes from a CSV the maintainer exports by hand; no
  login from Clipper.
- Posting context: the CORE (Stable Ronaldo, JasonTheWeen, Lacy, Silky,
  Marlon, Adapt); campaigns wanted are CORE, Lacy and JasonTheWeen, all
  three briefs read (see "Campaign briefs read").

From the V7 interview on 2026-11-12:

- Bad picks are still the costliest failure, and a mix: nothing
  happening, moments that are seen rather than said, and the right moment
  cut badly. The lever to try is a finer LLM score.
- Some posted clips come from gaming streams with a facecam box, so the
  facecam split is worth a spike.
- No Studio CSV export yet, so the performance work stays out of V7.
- In `review`: save a span that overlaps another clip's label by less than
  the hit rule, and mark cuts by hand, at the playhead and on the strip.
- The small Parked fixes ride along, each its own PR.

From the review editor questions on 2026-11-13:

- Everything offered slows review down: layout and scrolling, trimming
  precision, clicks per clip, and not seeing the result before Save.
- Every editing option offered would be used on most clips: caption
  editing, text styling, zoom and reframing, audio.
- A full redesign of the page, still plain JavaScript; before the finer
  LLM score.
- A chat box in the page to describe edits and have them made, with the
  edits the page has. Free only: the local LLM, not Claude through a
  paid API.
- A "Summarize" button that condenses a VOD's clips into one longer
  Short, for episodes of iterative games that ramp from boring tasks to
  huge goals; each clip can still be posted alone.

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
- V4: `clipper drafts` from a URL or file to a folder of vertical,
  word-captioned, titled drafts; `clipper fetch`; `render --vertical`,
  styled captions, `--titles` and a folder per video with a manifest.
  Released as `v0.4.0` (pre-release, tag on 603e410). Notes are in
  `docs/releases/v0.4.0.md`.
- V5: `clipper review` and judged labels in `eval`; `drafts` lists, the
  LLM and top 10 by default, `fit` and `face` styles, titles with video
  info and `--about`; heuristic end extension and a 15 s minimum.
  Released as `v0.5.0` (pre-release, tag on 9cda982). Notes are in
  `docs/releases/v0.5.0.md`.
- V6: cuts, on-screen text, thumbnails, ratings and people in `review`;
  `queue` and `posted`; drafts filed by channel and day; saved about text
  and emoji in titles; remux after `fetch`; title examples, detector
  examples and a trained ranker measured and not adopted. Released as
  `v0.6.0` (pre-release, tag on 13935cf). Notes are in
  `docs/releases/v0.6.0.md`.

In progress:

- A trial week by the maintainer (agreed 2026-11-03, no code): `drafts` on 5
  to 10 real target videos, noting which drafts would be posted, fixed or
  dropped, and writing label files for the moments worth clipping. V5 was
  scoped from the first four VODs' notes on 2026-11-09 (see "V5 scope");
  from V5 item 4 on, judgments are made in `review` as judged labels.
  Trial notes so far:
  - 2026-11-09, VOD 2922695332 (jasontheween, 6:31), `drafts` with the
    heuristic: drafts 4 (1:39:11-1:39:49, "Why He Never Asked Her Out") and
    5 (5:24:04-5:24:49, "The Best Part About Japan") are the best to post;
    1 to 3 dropped as boring moments, nothing happening: a picking failure,
    not a cut or title one. Neither pick overlaps a community
    clip, so by the maintainer's judgment the top 5 had 2 worth posting
    where the clip labels count 0.
  - 2026-11-09, VOD 2917872953 (stableronaldo, 5:04), `drafts` with the
    heuristic: all 5 worth posting (relatable gas prices, funny, a new
    iPhone). 4 of 5 need a couple of seconds more at the end, and the
    maintainer rewrote all 5 titles, naming the people and the angle
    ("Ron shocked at the gas pump", "Core boys at the pump", "Ron's got new
    tech") where the model wrote generic ones ("$134 to fill a tank"). The
    clip labels count 0 of 5 here too.
  - Why clips end early: the files match their spans exactly; a candidate
    ends at its last segment's last word (`sliding_windows`), and the next
    speech starts 0 to 0.74 s later on 4 of 5. Drafts 1 and 3 are one
    moment (the gas pump) split into neighbouring windows 2.5 s apart. Both
    are the boundaries item under "Later" (V5 picking).
  - 2026-11-09, VOD 2922589775 (adapt, 5:18), `drafts` with the heuristic:
    only draft 2 (2:00:28-2:01:13, the Warzone pirate token) worth posting,
    retitled "Adapt learns about the Pirate Token" (the model wrote "New
    Warzone Pirate Token Explained"). Drafts 3 and 5 dropped as nothing
    happening; 1 and 4 dropped as Adapt reacting to a compilation (4 is a
    compilation of himself), all over the place with no firm topic. A
    picking failure again. The clip labels count 0 of 5.
  - 2026-11-09, VOD 2924046310 (ohnepixel, 4:03), `drafts` with the
    heuristic: 3 of 5 worth posting. Draft 1 (0:19:42-0:20:27, marketing
    to IT) as is, relatable to students. Draft 4 (0:17:42-0:18:24) needs a
    couple of seconds more at the end; its title is good. Draft 5
    (2:40:42-2:41:26) cuts off right after he opens the food, before he
    tries it. Draft 2 dropped as boring; draft 3 (0:16:43-0:17:28) dropped
    for lacking context, unless it gets more at the start or end. Draft 3
    is the only one that hits a clip ("dutch bars"), so the one clip hit
    in five VODs' top 5 is a draft the maintainer would not post as cut.
    Two of the three keepers need more at the end: the boundaries item
    again.
  - 2026-11-10, YouTube 5pk8HLcAAT0 (a business podcast, about 2:13),
    `drafts` with the LLM, judged in `review` (labels in
    `data/labels/picks/5pk8HLcAAT0.json`): 9 of 10 kept, 1 rejected, the 9 to be
    posted as Shorts by hand. 8 keepers are `fix`: 7 ends moved
    later by 6.0 to 39.3 s (the LLM has no end extension, ADR 0023 item 5)
    and 2 starts earlier by 2.5 and 5.0 s. The podcast niche, so outside
    the V5 streamer comparisons.
- Labels from Twitch community clips (option B), done: ADR 0012 and 0020
  amendments #116, `clipper labels` #117, manual steps T0 to T6, and the
  first labels, `data/labels/v2917438534.json` from 5 clips (links in
  `data/labels/links/`). Heuristic `eval` on it: recall 0 of 5 at top 5, 1 of
  5 at top 30 (precision 1 of 30). LLM (`qwen3.5:9b`): 0 of 5 at top 5
  (822 s) and at top 30. Second VOD, `v2922695332.json` (4 clips, from the
  maintainer's first trial `drafts` run): 0 of 4 for both detectors at top 5
  and top 30; two of its clips have almost no speech. Third VOD,
  `v2917872953.json` (5 clips): 0 of 5 for both, though the maintainer would
  post all 5 heuristic drafts. Fourth VOD, `v2922589775.json` (5 clips): 0
  of 5 for both; the LLM took 1,168 s and scored all its top 30 0.85 to
  0.95. Fifth VOD, `v2924046310.json` (6 clips): heuristic 1 of 6 at top 5,
  2 of 6 at top 30; LLM 0 at top 5, 1 of 6 at top 30 (707 s). Over 25 clips
  the heuristic found 1 at top 5 and 3 at top 30, the LLM 1 at top 30. Clips
  and the maintainer's picks disagree, so clip labels are a weak stand-in
  for "worth posting"; trial notes count more. Small samples.
- Campaign briefs read (2026-11-10 and 11-11), four so far. None asks
  for a detector input; all ask for work at the edit and post stage.
  - Lacy (a pool campaign, paid by share of views): clips from Lacy's
    Twitch, YouTube and other accounts, Lacy the main focus, Lacy's name in
    the caption or on-screen text and `#Lacy`, no logos, not negative, no
    reposting; YouTube Shorts is allowed. Nothing Clipper lacks beyond
    `--about "Lacy"` for titles (V5 8c); the rest is checked in review and
    at upload.
  - The Iced Coffee Hour (Clip Strategy; the Greg LaVecchia episode,
    5pk8HLcAAT0): cut from the campaign's own files, not a download; English
    captions with names and numbers checked and the key word highlighted;
    an on-screen hook over the first seconds; original B-roll edited in;
    tags per platform. Dropped by the maintainer: B-roll is beyond Clipper
    (stock or generated footage clashes with the no-paid-services
    non-goal), and the hook and caption fixes would be manual too. The
    judged labels from its drafts stay (#151).
  - CORE (pool, $20,000 by share of approved views, 2,000,000 views
    minimum, ends 2026-11-30): any video, stream or clip featuring at
    least 2 CORE members; the same clip may also go to the Lacy and
    JasonTheWeen campaigns if it meets theirs. TikTok, Reels, YouTube
    Shorts or X; audience at least 50% from native English-speaking
    countries, account in a related niche. Caption or overlay names CORE
    or a member, `#CORE`; English only; no logos, collab posts, reposts or
    botting; not negative, no hate farming or negative false narratives.
    Wants funny moments, reactions and banter between 2 or more members.
    Clipper's part: which members are on screen, which the transcript
    can only hint at; checked in review.
  - JasonTheWeen (pool, 700,000 views minimum, ends 2026-11-30): any
    video featuring JasonTheWeen, on any channel; caption or overlay names
    JasonTheWeen, `#JasonTheWeen`.
- Back burner, decided later: audience signals (YouTube "most replayed",
  chat activity, viewers over time, Twitch community clips). Checked
  2026-11-03 with yt-dlp metadata: a heatmap on 1 of 3 YouTube videos, no
  chat replay on any, no viewer history after a stream, no Twitch VOD chat
  via yt-dlp. Viewer counts need live monitoring, which ADR 0020 rules out.

V5 baseline (item 4, 2026-11-10), the reference for items 5 to 7, 10b, 11
and 12. Heuristic and LLM (`qwen3.5:9b`) top 10 on the four trial VODs,
drafts in `data/baseline/<detector>/`, every draft judged in `review`,
labels pooled in `data/labels/picks/` (committed). `eval` at top 10 against
them; "keepers" are drafts on a `post` or `fix` label, "cut short" are
keepers whose label ends over 1 s later or starts over 1 s earlier.

| VOD | Detector | Keepers (as cut) | Cut short | Rejected | Unjudged | End gap median (max) |
|---|---|---|---|---|---|---|
| v2922695332 (jasontheween) | heuristic | 3 (2) | 1 end, 1 start | 7 | 0 | +0.0 s (+8.1) |
| | LLM | 6 (2) | 4 end | 4 | 0 | +14.0 s (+36.7) |
| v2917872953 (stableronaldo) | heuristic | 8 (4) | 4 end | 2 | 0 | +1.2 s (+29.7) |
| | LLM | 7 (6) | 1 end | 3 | 0 | +0.0 s (+15.8) |
| v2922589775 (adapt) | heuristic | 2 (0) | 1 end, 1 start | 8 | 0 | -8.1 s (+6.4) |
| | LLM | 5 (3) | 1 end, 1 start | 5 | 0 | +0.0 s (+18.2) |

- Over the three VODs: heuristic 13 keepers, LLM 18, of 30 drafts each.
  Pooled labels: 9, 14 and 7 keepers, and 11, 5 and 13 rejected spans.
- Clip labels (Twitch community clips): 0 hits at top 10 and top 30 for
  both detectors on these three VODs.
- Time per VOD with cached transcripts: heuristic about 2 to 3 min, LLM 15
  to 17.4 min, each rendering 10 vertical, captioned, titled drafts.
- v2924046310 (ohnepixel), apart: the downloaded file started at 64 s, so
  the page's player ran 64 s off while it was judged; the labels were moved
  by -64 s to the footage watched, and most no longer sit on the drafts'
  spans. Heuristic 3 keepers, 1 rejected, 6 unjudged; LLM 6 keepers, 4
  unjudged. 12 labels (2 `added`), 10 rejected spans. Clip labels: heuristic
  2 of 6 at top 10 and 30, LLM 1 of 6. Re-judge its unjudged drafts on the
  remuxed file before counting it.
- v2917438534 (its clip labels only) was left out of the baseline.

More judged drafts (2026-11-10), after the baseline: `drafts` with the LLM
at top 10, every draft judged in `review`, labels in `data/labels/picks/`.
Keepers saved and drafts trashed per VOD:

- v2922292514 (stableronaldo, Twitch): 5 keepers, 5 trashed.
- v2924380274 (jasontheween, Twitch): 6 keepers, 4 trashed.
- 3Sbj_BZORr4 (CORE, YouTube IRL): 7 keepers, 3 trashed.
- ANoZNWQXYZ4 (CORE, YouTube IRL): 10 keepers, none trashed.
- XwH_o5xboKA (Lacy, YouTube stream): 10 keepers, none trashed; rendered
  before item 13d, so no style.
- v2910908943 (stableronaldo at Jason's NMS Boxing, Twitch): 7 keepers (3
  `fix`, each end moved later by 3.7 to 11.6 s), 3 trashed.
- Styles (ADR 0025): the other five rendered `fit` and `face`, and all 35
  keepers were saved in `fit`, none in `face`. Input for the speaker
  detection and facecam split items under "Later".

Loudness spike (V5 item 10a, 2026-11-10), ended in nothing. Per-second
loudness from ffmpeg's `ebur128` (`-v verbose` prints the 100 ms frame log;
the max momentary and short-term value per second kept), about 30 to 45 s per
VOD, on the five judged streamer VODs with files: v2917872953, v2922695332,
v2922589775, v2924380274, v2922292514 (ohnepixel left out for its start
offset). 42 keepers (`post`, `fix`, `added`), 40 rejected spans, 14 Twitch
clip labels, and 200 random 45 s spans per VOD. Per span: peak short-term
loudness over the VOD's median, the 90th percentile of momentary loudness,
the rise against the 60 s before, and the share of seconds above the VOD's
90th percentile. Scratch scripts, not committed.

| Feature | Keepers over rejected (AUC) | Keepers over random | Clip labels over random |
|---|---|---|---|
| Peak short-term | 0.56 | 0.54 | 0.61 |
| 90th percentile momentary | 0.56 | 0.55 | 0.68 |
| Rise against the 60 s before | 0.58 | 0.59 | 0.68 |
| Share of loud seconds | 0.52 | 0.52 | 0.63 |

- Keepers against rejected spans sit inside chance (0.37 to 0.63 for 42
  against 40 by simulation), and per VOD the direction flips: peak loudness
  0.37 on stableronaldo, 0.79 on the 2026-11-09 Jason VOD.
- Community clips lean loud (0.61 to 0.68 over random spans), on 14 clips;
  the maintainer's keepers do not. Clips and the maintainer's picks already
  disagree (see the clip labels above).
- The one mostly silent keeper (jasontheween 5:16:02, lost to the LLM in
  ADR 0023 item 6) is loud: peak short-term 17.1 LU over the VOD's median,
  above 97% of same-length windows. One example, which louder-is-better
  would find, against no difference over all keepers.
- Decision: loudness is not a detector input (10b dropped). Revisit if
  silent or visual keepers become common in judged labels, or the facecam
  or speaker work (ADR 0025) needs an audio signal.

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
- `opencv-python-headless>=5.0.0.93,<6` — not a pin, but 4.x would pull
  `numpy` down from 2.5.4 (ADR 0003, face tracking amendment).
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
7. ~~GPU transcription (was V3 item 5): time the long video on the GPU
   against the CPU's 20:59. May end in a settings change or in nothing.
   Manual-test step. No `eval` claim.~~ (done; ended in nothing: 593 s on the
   GPU, but the transcript drifted; ADR 0021, #106)
8. ~~One command from a URL or file to a folder of drafts: fetch, analyze,
   render with `--vertical --captions --titles`. Its command shape needs an
   ADR or a 0011 amendment, decided in that item.~~ (done; `clipper drafts`,
   ADR 0022 #108, extraction #109, command #110, manual steps D8 to D10)
9. ~~Release notes and tag `v0.4.0`, if the items above land.~~ (done; notes
   #112, F1 run before tagging, tag `v0.4.0` and pre-release by the
   maintainer)

## V5 list

Each item is one PR, and each code item states its design and waits for
confirmation first. Measurement first, then the review page, then picking.
Every picking change (items 5 to 7, 10b, 11, 12) is measured before and
after on the trial VODs at top 10 against the judged labels: postable drafts
per VOD, drops, keepers cut short, seconds dragged at start and end
(median and max), and clip-label recall at 10 and 30 as a secondary check,
with unjudged candidates judged first. Counts per VOD, not percentages.
Items 8 and 9 do not depend on picking and may land earlier.

1. ~~ADRs and scope (one docs PR): the V5 scope and list in CLAUDE.md, ADR
   0012 amendment (judged labels), ADR 0023 (boundaries), ADR 0024 (review
   page).~~ (done; #126)
2. ~~Judged labels: the file format (`post`, `fix` with the draft's original
   span, rejected spans, notes) in `data/labels/picks/`, and `eval`
   reporting unjudged candidates, keepers cut short and seconds dragged.
   ADR 0012 amendment.~~ (done; #128; verdicts `post`, `fix`, `added`, and
   gaps at each end in place of seconds dragged)
3. `clipper review` (ADR 0024), in PRs:
   - 3a. ~~Server, clip list and a player on the source video over the clip
     plus 120 s either side. Decides the HTTP server (standard library or a
     framework, checked against 0003) and where the code lives. Manual
     step.~~ (done; #130; standard library, `src/clipper/review/`, manual
     steps RV0 to RV4; RV2 and RV3 run; RV1, run later by the maintainer,
     found the range-stop bug fixed in #132)
   - 3b. ~~Film strip from cached ffmpeg thumbnails, with start and end
     handles. Manual step.~~ (done; whole-VOD player and step buttons #132,
     film strip #133; manual steps RV5 and RV6 passed, run by the
     maintainer)
   - 3c. ~~Save, Trash, title and notes, writing judged labels and the
     manifest (ADR 0019 amendment). Unit tests on the endpoints; manual
     step.~~ (done; #135; ADR 0024, 0019 and 0018 amendments; manual step
     RV7 passed, run by the maintainer)
   - 3d. ~~Re-render one clip on Save (ADR 0011 amendment). Manual step.~~
     (done; #137; ADR 0024, 0019 and 0011 amendments; manual step RV8
     passed, run by the maintainer, who found Reset leaves an edited title
     and the model's title is not kept; a follow-up PR)
   - 3e. ~~Add clip from the current range. Manual step.~~ (done; #140;
     from the playhead anywhere in the VOD, verdict `added`; ADR 0024 and
     0019 amendments; manual step RV9 passed, run by the maintainer)

   Item 3 is done: whole-VOD player #132, film strip #133, Save/Trash #135,
   re-render #137, model title #139, Add clip #140.
4. ~~Not code, a docs PR: the baseline. Heuristic and LLM top 10 on the trial
   VODs (`CLIPPER_TOP_N=10` in a child process, cached transcripts; ask
   before any download), every draft judged in `review`, the first judged
   labels committed and the measures above recorded.~~ (done; results under
   "V5 baseline" in "Current state"; ADR 0023 amendment; the `eval` draft
   span fix #144 and the review start offset fix #143 came out of it)
5. ~~End extension to the next pause, up to 90 s, never into the next chosen
   candidate; the pause length set from item 4's dragged ends. ADR 0023
   amendment.~~ (done; #146; pause 1.5 s, heuristic only: early ends 6 to
   1, ends over 5 s long 1 to 4; the LLM's stayed 6 early and went 1 to 7
   over, so not adopted there)
6. ~~Candidate length 15 to 90 s for both detectors (windows under 15 s not
   scored). ADR 0023 amendment. Replaces Parked "Candidates too short to
   post".~~ (done; #148; heuristic unchanged, LLM 18 keepers and 12 rejects
   either way, none under 15 s, one mostly silent keeper lost)
7. ~~Merge neighbouring high-scoring windows into one candidate of up to
   90 s. ADR 0023 amendment.~~ (dropped, not built; ADR 0023 item 7
   amendment: of five neighbouring pairs in the top 10s, none would gain,
   and the gas pump pair was judged as two postable clips)
8. Title context:
   - 8a. ~~`fetch` saves yt-dlp's video metadata next to the video, including
     for a reused file if yt-dlp allows it (unverified). ADR 0020
     amendment, manual step.~~ (done; #152; `<id>.info.json` from a
     `--skip-download` request, so reused files too; manual step D10a,
     run in part: Twitch gives the login, YouTube the channel name)
   - 8b. ~~The title prompt gets the channel and VOD title; nothing for local
     files. ADR 0018 amendment, manual step; trial measure: titles kept
     unedited.~~ (done; #154; titles naming the person on 39 reviewed
     keepers: 1 before, 12 with video info, 24 with names by hand; manual
     step R19a)
   - 8c. ~~`--about "<text>"` on `drafts` and `render`: names and context
     written by hand (a campaign brief's people, "Ron (stableronaldo)"),
     sent to the title prompt with the video info, for local files too.
     Measured in 8b: 24 of 39 titles name the person with written names,
     12 with video info alone. ADR 0018 amendment, manual step.~~ (done;
     #156; 22 of 39 with `--about`; manual step R19b)
   - 8d. ~~No guessed pronouns in titles: the prompt takes he/she only from
     pronouns stated in `--about`. ADR 0018 amendment; measured on the
     drafts folders' titled clips.~~ (done; #173; Lacy as she/her 4 to 2
     of 10, 0 with pronouns stated; manual step R19b extended)
9. ~~`drafts` with a list of sources; one failing source does not stop the
   rest, and the exit code is 1 at the end. ADR 0022 amendment, manual step.~~
   (done; #169; several sources or `--list`, `| about` per line, all
   checked first, a summary table; manual step D12)
10. Audio loudness:
    - 10a. ~~Spike: per-second loudness with ffmpeg on two trial VODs,
      compared with the judged keepers and the clip labels. Ends in a new
      ADR or in nothing. Manual step.~~ (done; ended in nothing, five VODs
      instead of two, results under "Loudness spike" in "Current state";
      no code, so no manual step)
    - 10b. ~~If 10a says so: loudness as a detector input, a `core/` change.
      Measured on judged and clip labels.~~ (dropped: 10a did not say so)
11. ~~Defaults for `drafts`: detector (heuristic or LLM) and top N, from the
    measures above; a finer LLM score if ties decide it (Parked). ADR 0014
    amendment.~~ (done; #171; LLM and `--top 10` for `drafts` only, 18 to
    12 keepers at top 10; ties did not decide it, so the finer score stays
    Parked)
12. ~~A larger Whisper model (`small`) on the CPU, timed on the long stream
    and measured as above. May end in nothing. ADR 0021 amendment, manual
    step.~~ (done; ended in nothing: a one-VOD pilot on v2922589775 instead
    of the long stream, 2,415 s, LLM keepers 5 to 0 with 9 unjudged; ADR
    0021 amendment; no code, so no manual step)
13. ~~Face-aware vertical spike: a facecam found once per video and split
    above the gameplay, following the person, or nothing. Ends in a new ADR
    amending 0016; built in V5 only if it picks the facecam split.~~ (done;
    ADR 0025: a `face` crop on the largest face, and picking a style per
    clip, built in V5 as below; the facecam split later)
    - 13a. ~~ADR 0025 and the V5 scope (one docs PR).~~ (done; #159)
    - 13b. ~~Face tracking: a `FaceTracker` Protocol, an OpenCV YuNet
      adapter, 5 samples a second, cached under `<cache dir>/faces/`, the
      model downloaded once with its SHA-256. ADR 0003 check for
      `opencv-python-headless` 5.0.x and `numpy`.~~ (done; #160; the model
      is kept in the package, MIT licence; ADR 0025 and 0003 amendments)
    - 13c. ~~The `face` style in `render`: a full-height crop on the largest
      face, dead zone, easing, a jump at a camera cut. Manual step.~~ (done;
      #162; `render --vertical --style face`, manual step R20; Save in the
      review page re-renders in `fit` until 13d/13e)
    - 13d. ~~Several styles in `drafts` (`fit` and `face`) and variants in
      the manifest (ADR 0019 amendment).~~ (done; #165; locked framing fix
      #164 first, after the maintainer found 13c choppy)
    - 13e. ~~A style switch in the review page; Save keeps the chosen style
      and records it (ADR 0024 amendment). Manual step.~~ (done; #167;
      manual step RV10 run 2026-11-10 by the maintainer: steps 1 to 4 and
      7 passed, 5 and 6 not reached on two clips)

    Item 13 is done: ADR 0025 #159, face tracking #160, face style #162,
    locked framing #164, both styles in `drafts` #165, style switch #167.
14. ~~Release notes and tag `v0.5.0`.~~ (done; notes #179, F1 run before
    tagging, tag `v0.5.0` and pre-release by the maintainer; RV10 and D12
    run after the release by the maintainer, RV10 in part, and their steps
    fixed)

## V6 list

Each item is one PR, and each code item states its design and waits for
confirmation first. Learning from labels first, then performance (ADR
0026). Every learned change is measured per VOD with that VOD left out of
what it learned from, against the current default: keepers and rejects in
the top 10, keepers cut short and end gaps, counts per VOD; for titles,
titles naming the person and titles kept unedited.

1. ~~ADRs and scope (one docs PR): ADR 0026 (learning), ADR 0027
   (performance data), the V6 scope and list.~~ (done; #182, with small
   networks on frozen embeddings and nearest-clip lookup allowed)
2. Not code: the maintainer exports one YouTube Studio CSV of the
   channel's Shorts; its columns, the count of Shorts and whether it has
   retention or swipe-away figures go into an ADR 0027 amendment.
   (Moved to a later version with items 6 to 9.)
3. ~~Title examples: the title prompt gets the maintainer's own titles (where
   `title` differs from `model_title`) as examples, chosen from other VODs.
   Measured on held-out VODs' keepers. ADR 0018 amendment, manual step.~~
   (done; ended in nothing: naming the person 48 to 32 of 110, the
   maintainer's person 28 to 18 of 65, more swear words, with either
   format; ADR 0018 amendment; no code merged)
3a. ~~Saved about text per channel (added after item 3): a channels file of
   `<channel> | <about text>`, used for titles when no `--about` is given.
   ADR 0018 amendment, manual step R19c.~~ (done; #185; naming the person
   48 to 68 of 110)
3b. ~~Emoji in titles (added after item 3, the maintainer's choice): the
   title prompt asks for fitting emoji in place of "no emoji". ADR 0018
   amendment, measured on the same keepers.~~ (done; every title ends with
   emoji, naming the person 68 to 61; ADR 0018 amendment)
4. ~~Detector examples: the LLM prompt gets judged keepers and rejects from
   other VODs as examples. Measured leave one VOD out with `eval`, with the
   time per VOD. ADR 0014 amendment. May end in nothing.~~ (done; ended in
   nothing: on four VODs at top 10, 29 keepers and 11 rejects without, 25
   (27 with two overlapping saves `review` refused) and 12 with; the 22
   new windows judged, labels committed; ADR 0014 amendment; no code)
4a. ~~Rating and people in `review` (added 2026-11-11, the maintainer's
   choice): Good or OK on each keeper, and who is on screen from a people
   file, on the judged label. ADR 0024 amendment, manual step RV11.~~
   (done; #190)
4b. ~~A post queue (added 2026-11-11): the Good, unposted keepers across
   drafts folders, interleaved by VOD, with titles, captions and campaign
   tags, and a way to mark each posted with its date and YouTube link (the
   link also serves ADR 0027's matching). Design stated first.~~ (ADR 0028;
   `clipper queue` and `clipper posted`; manual step Q1)
4c. ~~Cut pauses in review (added 2026-11-11, the maintainer's choice; ADR
   0029), in PRs: the ADR; the time map, pause finder, captions and face
   crop through the map, the renderer joining kept parts and `cuts` on the
   label and manifest; then the page's Cut silence, toggles, Preview and
   Save with a manual step.~~ (done; ADR #192, core #194, handles fix
   #195, page with manual step RV12)
4e. ~~On-screen text and thumbnails (added 2026-11-11, the maintainer's
   choice; ADR 0030), in PRs: the ADR; text rendering (the model, ASS
   events, manifest and label); text in the page with a manual step;
   thumbnails with a manual step.~~ (done; ADR #198, rendering #199, page
   with manual step RV13, thumbnails with manual step RV14)
4d. ~~Drafts filed by channel and day (added 2026-11-11, the maintainer's
   choice): `<clips dir>/<channel>/<MM-DD-YYYY>/<stem>/`, the day the
   drafts ran, an existing folder reused; queue and `posted` find nested
   folders. ADR 0019 amendment, manual step Q2.~~ (done)
5. ~~A trained ranker on the judged labels (frozen local text embeddings and
   a simple classifier, with the heuristic's signals, the LLM score and
   nearest judged spans as inputs if they help), against the LLM leave one
   VOD out. Keeps the embeddings in a file (ADR 0026 decision 5). Decides
   the libraries (ADR 0003 check), where the model file lives, and how it
   is chosen (ADR 0013). May end in nothing.~~ (done; ended in nothing:
   keeper over reject 0.59 to 0.61 on held-out VODs, its own top 10 almost
   all unjudged; ADR 0026 amendment; no code)
6. `clipper` CSV import (command name decided in the item): parse the
   export from item 2, link Shorts to drafts by matching, confirm ambiguous
   ones, keep confirmed links and a dated snapshot. ADR 0027 amendment,
   manual step. (Moved to a later version.)
7. A performance report: linked Shorts against their verdicts, detector
   scores, styles and titles, compared by age and channel. Sets whether
   performance can be a target. Not a detector change. (Moved to a later
   version.)
8. If item 7 says so: performance as a second training target for item 5's
   ranker. ADR 0026 amendment. May end in nothing. (Moved to a later
   version.)
9. Retraining as one command from the current labels (and links, after
   item 8), printing held-out measures beside the current model's before
   replacing it (ADR 0026 decision 6). Waits for a ranker that is adopted
   (item 5 was not). (Moved to a later version.)
10. ~~Not learning, from daily use: remux after `fetch` for files whose
    timestamps do not start at 0 (Parked), so `review` opens every
    download. ADR 0020 amendment, manual step. May land any time.~~
    (done; `Remuxer`, new and reused files, manual step D13; no late file
    was at hand to check, v2924046310 already remuxed by hand)
11. ~~Release notes and tag `v0.6.0`.~~ (done; notes #205, README
    review and queue sections, F1 run 2026-11-12 before tagging, tag
    `v0.6.0` and pre-release by the maintainer)

## V7 list

Each item is one PR, and each code item states its design and waits for
confirmation first. Review first (items 2, 3 and 9), then picking, then
framing; the fixes in item 7 may land any time. Item 9 was added on
2026-11-13 and goes before item 5.

1. ADRs and scope (one docs PR): the V7 scope, goals and list. ADRs come
   with each item as amendments.
2. ~~Overlap on Save: `review` saves a span that overlaps another clip's
   label by less than half the shorter span (the hit rule, ADR 0012), so
   `eval` still counts them apart, and refuses above that (409, as now).
   ADR 0024 amendment, unit tests, manual step.~~ (done; ADR 0024 and
   0012 amendments, `spans_hit`, manual step RV15)
3. Manual cuts (ADR 0029 "later"), in PRs:
   - 3a. ~~Cut in and out at the playhead, by buttons and keys, added to the
     cut list beside the pause cuts, toggled, previewed and saved in
     `cuts` the same way. ADR 0029 amendment, manual step.~~ (done;
     keys `[`, `]`, Esc; Cut silence keeps manual cuts; manual step RV16)
   - 3b. ~~Drag a range on the film strip to cut it. Manual step.~~
     (done; over 4 px is a drag, a click still seeks; RV16 steps 9, 10)
4. ~~Move `OllamaClient` to a shared adapter module with its own error,
   behaviour-neutral (Parked), before item 5 changes the prompt.~~ (done;
   `adapters/llm/ollama.py`, `LlmError`; ADR 0014 amendment)
5. A finer LLM score (Parked): a scoring rubric in the prompt, so scores
   stop clustering on round values; token probabilities only if Ollama
   returns them (unverified). Measured on the judged VODs at top 10
   against the current prompt, with the time per VOD and the count of
   distinct scores. ADR 0014 amendment. May end in nothing.
6. The facecam split (ADR 0025), in PRs:
   - 6a. Spike: find a fixed facecam box per clip on a few gaming VODs,
     checked against boxes marked by hand. Ends in an ADR 0025 amendment
     or in nothing.
   - 6b. If 6a says so: a `facecam` style in `render` and `drafts`, the
     box stacked above the gameplay; a clip with no box is a note and
     renders in `fit`. Manual step.
   - 6c. The `facecam` style in the review page's style switch (ADR 0024
     amendment). Manual step.
7. Small fixes from "Parked", each its own PR:
   - 7a. ffmpeg exit codes: signed `AVERROR` values, with short names for
     invalid data, no stream and permission denied.
   - 7b. The repeated prefix in `render`'s malformed-JSON message.
   - 7c. Letterboxed sources under `--vertical`: fit only the picture area
     (`cropdetect`, untried here). ADR 0016 amendment, manual step.
   - 7d. Docs and housekeeping: `docs/architecture.md` drift and code
     fences, mojibake in `docs/roadmap.md` and `docs/architecture.md`,
     duplicate `.gitignore` entries, the D9 and D10 pipe.
   - 7e. ~~On-screen text that wraps (added 2026-11-13, found by the
     maintainer): one box behind all lines, not a box per line that
     overlaps its neighbour. ADR 0030 amendment, manual steps.~~ (done;
     `BorderStyle 4`, needs libass 0.17 or newer; RV13 step 7, RV14
     step 6)
   - 7f. ~~`clipper posted` takes relative paths (added 2026-11-13, found
     by the maintainer): from the current folder or the clips dir. ADR
     0028 amendment, manual step.~~ (done; Q1 extended)
   - 7g. ~~`clipper posted "<title>" <link>` (added 2026-11-13, the
     maintainer's ask): the title as the queue shows it. ADR 0028
     amendment, manual step.~~ (done; Q1 extended)
8. Release notes and tag `v0.7.0`.
9. The review editor (ADR 0031), before item 5, in PRs, each with a
   manual step:
   - 9a. ~~ADR 0031, the scope and a layout mock (one docs PR).~~ (done)
   - 9b. ~~Layout: clip list, preview, inspector tabs and timeline on one
     screen; no behaviour change.~~ (done; `Source`/`Draft` switch until
     9e, three tabs, manual step RV17)
   - 9c. ~~Keys: play, J/K/L, frame step, start and end, cuts, rating,
     Save, Trash, next and previous, auto-advance, a `?` list.~~ (done;
     `static/keys.js`, manual step RV18)
   - 9d. ~~Precision: a waveform from cached ffmpeg peaks, frame stepping,
     snapping to word edges, timeline zoom below a second.~~ (done;
     frame stepping came with 9c; manual step RV19)
   - 9e. ~~Live preview: the span through its cuts, framed as the style,
     with captions, texts and zooms drawn by the browser.~~ (done; zooms
     join with 9h; manual step RV20)
   - 9f. ~~Caption editing: fix a word's text (time kept), key words and a
     highlight colour. ADR 0017 amendment.~~ (done; hidden words too;
     manual step RV21)
   - 9g. ~~Text styling: colour, box or outline, size, fade-in, free
     position by dragging. ADR 0030 amendment.~~ (done; manual step
     RV22)
   - 9h. ~~Zoom and reframing: punch-ins over a range, a crop set by hand.
     ADR 0016 and 0025 amendments.~~ (done; vertical only; 8 s render
     2.4 to 2.8 s with both; manual step RV23)
   - 9i. ~~Audio: clip volume, a music bed lowered under speech, sound
     effects, from local `data/music/` and `data/sfx/`.~~ (done; manual
     step RV24)
   - 9j. Assistant: a spike first on requests the maintainer writes (the
     local LLM turning them into the page's edits), then an Assist tab
     if it is right often enough. ADR 0014 and 0031 amendments. May end
     in nothing.
   - 9k. Combine, first called Summarize (added 2026-11-13, the
     maintainer's choice; renamed the same day): a button in `review`
     that joins a VOD's kept clips, as edited, in VOD order into one
     longer Short (`<stem>_combined.mp4`), with optional label cards
     between them, a length warning past the Shorts limit, and its own
     manifest entry to title, rate and queue. ADR 0032, in two PRs:
     - 9k-1. ~~The summary: dialog, join, manifest entry, title, rating,
       thumbnail, trash.~~ (done; manual step RV25)
     - 9k-2. ~~Queue and `posted` for the summary (ADR 0028 amendment).~~
       (done; manual step Q3)
     - 9k-3. ~~Rename to Combine (the button, file, manifest field and
       code), so Summarize can mean the episode's story.~~ (done)
   - 9l. Summarize (added 2026-11-13, the maintainer's choice): a Short
     telling a VOD's story in 6 to 10 beats picked from the whole
     transcript by the local LLM (notes per ~3-minute chunk, then the
     beats, then spans snapped to sentences and pauses), edited and
     rendered in `review` with Combine's join. A spike first on an
     incremental-game VOD (DQP3pmyJX4o, That Damn Grass, 80 min),
     judged by the maintainer; may end in nothing. Then an ADR 0033.

Later, not scheduled:

- A URL box in the review page, after item 9, once the drafts chain moves to
  `core/` (ADR 0022 "revisit when").
- Starts that move too, if the baseline shows starts dragged as often as ends
  (ADR 0023).
- Tuning for the education/speech and podcast niches, with their own judged
  labels.
- The movies/TV niche, after an ADR on rights.
- Whisper `medium`, or the GPU setup in ADR 0021: item 12's `small` pilot
  did not help, so only with a new reason a larger model would pick better.
- Campaign briefs as a detector input, once the maintainer has read some.
  Needs an ADR.
  Four read by 2026-11-11 (see "Campaign briefs read"); none asks
  for one. One asked for an on-screen hook, a possible small render item.
- Speaker detection for the face style (ADR 0025, locked framing
  amendment): the largest face is sometimes the listener (a podcast
  two-shot with the speaker side-on); mouth movement at 5 samples a
  second did not tell them apart. Needs an audio-visual model; after
  13e's picks show how often `face` loses to `fit` for it.
- The facecam split (ADR 0025): find a fixed facecam box per clip, measured
  first, and stack it above the gameplay. After V5 item 13e's picks show
  how often facecam clips are posted. Scheduled as V7 item 6.
- Audio and video-frame embeddings as ranker inputs (ADR 0026), for the
  visual moments a transcript cannot see. After V6 item 5 shows the
  transcript-only ranker works; a frame encoder likely brings PyTorch with
  CUDA (several GB), so ADR 0003's check first.
- Rerun the V6 item 5 ranker spike once the judged windows about double
  (377 keeper and 204 reject windows on 15 VODs at the first run), or
  judge a ranker's own top 10 on a few VODs to score its picks.
- A small neural network in place of the ranker's classifier, once the
  labels are enough for it to do better on held-out VODs (ADR 0026).
- Nearest-clip lookup beyond item 5: prompt examples for the LLM detector
  chosen as the judged spans closest to each window, and "picked because
  it resembles these keepers" in the review page.

## Parked

- Twitch clip labels for v2924046310 (`data/labels/v2924046310.json`) may
  be 64 s off, if Twitch counts that VOD from 0 and the download began
  64 s in (its file was later remuxed to start at 0); unverified.
- ffmpeg's exit code prints as an unsigned 32-bit value on Windows, e.g.
  `exit 3199971767` for invalid data. Fix: normalise negative `AVERROR`
  values to signed form before display, and map the few codes this tool
  actually produces (invalid data, no stream, permission denied) to a short
  name, falling back to the bare signed number. `-1094995529` alone is no
  friendlier than `3199971767`. Seen again in manual step R14: exit
  `4294967283` is -13 (`EACCES`), cause "Permission denied". Scheduled as
  V7 item 7a.
- The `render` message for a malformed analysis JSON repeats the prefix:
  `Invalid JSON in analysis result <path>: Invalid JSON: ...`. The second
  part is pydantic's own message (manual step R11). Drop one of the two.
  Scheduled as V7 item 7b.
- Docs drift in `docs/architecture.md`: omits `AudioExtractor` from core
  interfaces; says the CLI depends on core, but it imports adapters; promises
  CLI config flags that don't exist; says ffmpeg is checked at startup, when
  the check is lazy. The diagram and layer text also need code fences so
  GitHub renders them correctly. Scheduled as V7 item 7d.
- Possible mojibake in `docs/roadmap.md` and `docs/architecture.md`.
  Scheduled as V7 item 7d.
- `.gitignore` duplicate entries (`.venv`, `dist/`, `__pycache__/`).
  Scheduled as V7 item 7d.
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
- A finer LLM score. Both models tried score on a few round values (on the
  long video, `llama3.1:8b` gave 0.8 to 403 of 1,215 windows), and neither
  put a label in its top 5 there. Options: token probabilities (whether
  Ollama returns them is unverified) or a scoring rubric in the prompt. Needs
  an ADR 0014 amendment and `eval` results. Part of V5 item 11 if ties
  decide the detector choice. Scheduled as V7 item 5.
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
  An ADR 0016 amendment. Scheduled as V7 item 7c.
- `llama3.1:8b` worded 2 of 6 reasons differently across two runs at
  temperature 0 and seed 0 (ADR 0015 "revisit when"). Not the default model.
- Title quality after the prompt fix (#114, `qwen3.5:9b`): lengths are
  within the limits on 35 clips, but one clip keeps "Ass" in its title under
  every prompt tried, and some hooks misstate the speaker or may add details
  (ADR 0018's title prompt amendment). Next levers if the trial shows it
  often: a rewrite request for listed words, or another model. Trial
  (v2917872953): the maintainer rewrote all 5 titles to name the people and
  the angle; the model sees only the clip's words and not who is on stream.
  Again on v2922589775: "Adapt learns about the Pirate Token".
  A lever: give the prompt the channel or known names. Needs an ADR 0018
  amendment. Scheduled as V5 item 8 (video metadata).
- `eval` ends with "Indicative only: one labeler, ..." even when the labels
  come from Twitch clips, which many viewers made. Fix: word it by source, or
  drop "one labeler"; how `eval` would know the source is undecided (a field
  in the labels file, or the note prefix). Seen in manual step T5.
- Manual steps D9 and D10 pipe `Invoke-Fetch` into
  `Select-Object -Last N`, which does nothing: the helper returns the text as
  one string. Fix: split the text into lines, or drop the pipe. Scheduled
  as V7 item 7d.
- yt-dlp warns on every YouTube request that no JavaScript runtime was found
  and "some formats may be missing" (it asks for deno). Downloads worked in
  manual step D1. Revisit if YouTube downloads fail or lose formats (ADR 0020
  "revisit when").
