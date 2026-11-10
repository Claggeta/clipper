# 0024: Review drafts in a local browser page

- Status: Accepted
- Date: 2026-11-09
- Amends: the "web UI" non-goal of V1 to V4 (vision), 0011 (one clip
  re-rendered on its own), 0019 (the manifest is edited between runs)

## Context

The maintainer reviews every folder of drafts by hand: watch each clip, note
whether to post, fix or drop it, and rewrite titles. In the trial week 6 of
11 keepers needed more at the end, and every judgment was written up by hand
afterwards. V5 measures picking changes against those judgments (0012 V5
amendment), which needs them recorded per draft with the span the maintainer
would have cut, and pooled across detector variants: more drafts to judge,
not fewer.

Fixing a clip today means cutting it again by hand outside Clipper. The
maintainer asked (2026-11-09) for a GUI: a list of clips, a player, a trim
slider showing the clip plus 2 minutes either side, a title box, a notes box,
and save and delete buttons, in a dark, clean style. It is for the period
until the drafts rarely need editing.

## Decision

1. **`clipper review <drafts folder>`** starts a local server on the
   loopback address and opens a page in the default browser. It reads the
   folder's `manifest.json` (0019), the analysis JSON it names and the source
   video. There is no URL input in V5: `drafts` makes the folder, `review`
   judges it.
2. **The page**: the clips listed down one side; a player; under it a film
   strip of the clip plus 120 s either side, clamped to the video, with two
   handles for start and end; a title box filled with the model's title; a
   notes box; Save, Trash and Add clip. Dark theme by default. Plain HTML,
   CSS and JavaScript shipped in the package, with no build step and no npm.
3. **The player plays the source video**, seeking inside the range, so
   dragging a handle needs no re-encode. Serving a multi-GB file to the
   browser needs HTTP range requests. Whether Python's standard library is
   enough or a framework is needed is decided in the first item, with a
   manual step, and any dependency is checked against 0003.
4. **The film strip** is thumbnails of the range made with ffmpeg through
   the shared helper (0004), cached per clip.
5. **Save** writes the clip's judged label (0012 V5 amendment): `post` if the
   span is unchanged, `fix` with the new span otherwise, and the notes. Then
   it re-renders that one clip with the folder's flags (vertical, captions)
   through the existing renderer, and updates its manifest entry, including
   the title as typed. What a failed re-render leaves behind is decided in
   its item; the label is written either way.
6. **Trash** deletes the clip file, marks its manifest entry dropped, and
   writes a rejected span to the judged labels.
7. **Add clip** marks a span the detector did not propose, from inside the
   current range, and saves it like any other clip: rendered, labelled
   `post`, listed in the manifest.
8. Loopback only, one user, no login. The server stops when the command
   stops.
9. **Tests**: the server's endpoints, and the label and manifest writes, are
   unit-tested with the fake renderer. The page itself is checked by manual
   steps; CI runs no browser.
10. Where the server code lives (under `cli/` or a front-end package beside
    it, as a second composition root using `AppContext`) is decided in the
    first item.

## Alternatives considered

- **A desktop window** (Qt, Tk). Tk has no video player. Qt's player on
  Windows depends on system codecs, and YouTube gave AV1 (0020), which
  Chrome plays; Qt was not tried. Both add a heavy dependency. Rejected.
- **Streamlit or Gradio.** Quick forms, but a custom two-handle film strip
  over a seeking player fights their models, and they are large
  dependencies. Rejected.
- **A keep/reject command in the terminal.** No video, so the maintainer
  still watches elsewhere and types times. Rejected.
- **The URL box now.** The page would own a 20-minute run with progress and
  failures, which is the second front end 0022 says should move the chain
  into `core/` first. Later, after the URL queue.
- **Record only on Save, re-render in a later batch.** Faster saves, but the
  folder would hold clips that do not match their labels until the batch
  runs. Rejected; the maintainer chose re-render on Save.

## Consequences

- (+) Judging drafts produces the labels V5 measures with, as a side effect.
- (+) Seconds dragged at each end measure the boundary work directly.
- (+) Fixed clips come out of Clipper, captioned and vertical, with no
  outside editor.
- (-) The first JavaScript in the repo, not covered by CI.
- (-) A later `render` or `drafts` run on the same folder replaces the
  manifest (0019 decision 3) and loses edits made in the page. The judged
  labels are separate files and survive.
- (-) Add clip only reaches moments within 2 minutes of a draft, so labels
  stay biased toward what some detector proposed.
- (-) The largest piece of V5, in several PRs.

## Revisit when

- The URL box is wanted: move the drafts chain into `core/` first (0022).
- Drafts rarely need editing: the page's job shrinks to judging.
- A second user or another machine needs it: loopback-only stops being
  enough.

## Amended by the review server PR (V5 item 3a)

- **Command.** `clipper review <folder> [--video PATH] [--port 8765]
  [--open/--no-open]`. Before serving: `load_manifest` (new, beside
  `save_manifest`, raising `ManifestError`); the video is
  `<CLIPPER_VIDEOS_DIR>/<manifest source>`, where `drafts` saves fetched
  VODs, unless `--video` names it; its name must match the manifest's
  `source` (`check_manifest_source`, compared like `check_source_matches`);
  the existing `MediaProbe` gives the duration and refuses a file with no
  video stream. Each failure is `Error:` and exit 1. Then it prints `Review:
  <url> (N clips; Ctrl+C to stop)` and serves until Ctrl+C.
- **Server: Python's standard library.** `ThreadingHTTPServer` bound to
  127.0.0.1, with daemon threads, since a browser opens several connections
  for one video. No dependency, so 0003 does not arise. A framework would add
  several packages for what one handler does.
- **Range requests** are a pure `parse_range(header, size)`: one `bytes=`
  range, with open-ended (`a-`) and suffix (`-n`) forms; `206` with
  `Content-Range`; `416` for a range at or past the end or an empty suffix;
  a malformed or multi-range header is ignored and the whole file sent, as
  HTTP allows. Dropped connections, which the browser makes on every seek,
  are ignored.
- **`SO_REUSEADDR` is off on Windows.** `http.server` sets it, and on
  Windows it let a second server bind a port already in use without error,
  found by the busy-port test. It stays on elsewhere, where it only allows a
  quick restart. A taken port is `Error: Cannot listen on 127.0.0.1:<port>:
  ...`.
- **Paths are a fixed list**: `/`, `/app.js`, `/style.css` (package files in
  `clipper/review/static/`, read with `importlib.resources`), `/api/clips`
  and `/video`. Anything else is 404, so no URL reaches another file. The
  query string is ignored. Requests are not logged.
- **`/api/clips`**: `source`, the folder's flags, `duration`, and per clip
  `rank`, `start`, `end`, `range_start` and `range_end` (120 s either side,
  `RANGE_SECONDS`, clamped to 0 and the duration), `title`, `hook`, `file`,
  `error`.
- **The page in 3a**: the dark layout, the clip list, and a native video
  player that jumps to a clip's start when it is chosen and pauses at the end
  of its range. The film strip and handles are 3b.
- **Where the code lives** (decision 10): `src/clipper/review/`, which takes
  built objects and imports no adapters. The `review` command in
  `cli/main.py` composes it, so `cli/` stays the only composition root.
  `AppContext.serve` starts the server, so CLI tests stub it instead of
  blocking.
- **Packaging**: `uv build` puts `static/` in the wheel (checked: the three
  files are under `clipper/review/static/`).
- **First real run** (2026-11-09, on `data/clips/v2917872953`, a 17.7 GB
  VOD of 5:07:44 by ffprobe, run by the assistant): the page files 200,
  other paths 404, `/api/clips` with all 5 clips and their ranges; the first
  1 KB `206` with `Content-Range: bytes 0-1023/17720256025`; the last 1 MB in
  2 ms and 50 MB from the middle in 49 ms; `416` past the end. A second
  server on the same port, a video for another source and a folder without a
  manifest each failed with their `Error:` line. The page in a browser (RV1)
  was not checked: the browser extension was not connected.

## Amended by the whole-VOD PR (V5 item 3a follow-up)

- **The player and the handles cover the whole video**, not the clip plus
  120 s: the maintainer asked to set a start or end anywhere in the VOD.
  Supersedes the range in decisions 2, 3 and 7 and the 3a page's pause at
  the end of the range. That pause was also a defect: seeking past the range
  and pressing Play snapped back to its end, and Play there paused at once
  (RV1, run by the maintainer).
- **Step buttons** under the player move the playhead by -15, -5, -1, +1,
  +5 and +15 s, clamped to the video. They move the playhead, not the
  handles; 3b's handles are planned to take the playhead's time.
- **`range_start` and `range_end` stay in `/api/clips`** as the film strip's
  starting view (planned for 3b: the clip plus 120 s, with zoom and pan; a
  strip over 5 hours is about 10 s per pixel). The page no longer shows the
  range.
- The (-) consequence that Add clip only reaches moments within 2 minutes of
  a draft no longer holds.

## Amended by the film strip PR (V5 item 3b)

- **Frames, one ffmpeg call each, on demand.** A `FrameExtractor` Protocol in
  `core/interfaces.py` (`extract(video, seconds, output) -> Path`, raising
  the new `FrameError`) and `FfmpegFrameExtractor` in
  `adapters/media/frames.py`, through `run_tool` (0004): `-ss <t>` before
  `-i`, one frame, scaled to 72 px high, JPEG quality 5. A run over the whole
  window would decode every frame in it and cannot serve a view that zooms
  and pans. `AppContext.frames` builds it; `review/` imports no adapter.
- **`GET /frames/<whole second>.jpg`**, 404 for any other form or a second
  at or past the duration. Frames are cached per video, not per clip
  (decision 4), at `<cache dir>/frames/<stem>-<size in bytes>/<s>.jpg`,
  written under a temporary name and renamed, so parallel requests never
  half-write one; the browser may keep them for a day. A `FrameError` is
  `500` and a blank tile. Nothing prunes the cache.
- **The strip** opens on the clip plus 120 s either side (`range_start` and
  `range_end`), as many 96 px frames as fit, at fixed steps of 1, 2, 5, 10,
  15, 30 or 60 s per frame on a grid, so views share cached frames. Zoom,
  pan by half a strip, Fit clip. Clicking a frame seeks.
- **Handles** for start and end, dragged (the player shows the frame under
  them; the strip pans past its edges) or set from the playhead with Set
  start and Set end. The span is at least 0.5 s, times to 0.01 s. Lengths
  outside 15 to 90 s (0023) are shown in red, not refused. Edits are kept
  per clip while the page is open; saving them is 3c.
- **Preview span** plays from the start handle and pauses at the end
  handle, checked every animation frame. It only stops a playback it
  started: a seek or a pause cancels it, so the 3a bug (a stop that also
  caught seeks) does not return.
- **Measured** (2026-11-09, `v2917872953`, by the assistant, no browser):
  about 220 ms per frame; a strip of 20 frames, fetched 6 at a time, 1.34 s
  cold and 0.02 s cached. A second past the end made ffmpeg exit non-zero.
  The page itself is checked by manual steps RV5 and RV6.

## Amended by the Save and Trash PR (V5 item 3c)

- **Judged labels** go to `<labels dir>/picks/<stem>.json` (the path in
  0012's V5 amendment), loaded when `review` starts. A broken file, one for
  another video, or one with labels without verdicts is `Error:`, exit 1,
  before serving.
- **Save** writes the clip's label: `post` if the span is unchanged to the
  millisecond, else `fix` with the draft's span in `draft`; the notes are
  the label's `note`. Then the manifest gets the title as typed and
  `reviewed: "saved"` (0019, 0018 amendments). No re-render yet (3d).
- **Trash** writes a rejected span with the notes, deletes the clip's file,
  then marks the manifest entry `reviewed: "trashed"` with no file. Each step
  can be repeated, so a Trash that failed part way (a file held open on
  Windows) is retried by pressing it again. Only a plain file name in the
  folder is deleted. The page asks for a second click within 3 s.
- **A clip's own judgment is found by its draft span**, to the millisecond:
  a `post` label's span, a `fix` label's `draft`, or a rejected span. Saving
  or trashing again replaces it, so the last judgment stands.
- **One label per moment.** A Save whose span overlaps another clip's label
  is refused (`409`), naming that label. Merging or replacing it would
  silently change an earlier judgment. Pooled drafts from two detectors
  (V5 item 4) can overlap; the maintainer trims one or trashes it as a
  duplicate.
- **Save on a trashed clip is refused** (`409`): its file is gone, and
  nothing re-renders it until 3d. A mistaken Trash is undone by editing the
  labels file by hand.
- **Endpoints**: `POST /api/clips/<rank>/save` (`{start, end, title, note}`)
  and `/trash` (`{note}`), answering with the clip as it now stands; `400`
  for a bad body (not an object, times outside the video or not numbers, end
  not after start, a title over one line or 200 characters, over 64 KB),
  `404` for no such rank, `409` as above, `500` for a write failure. Writes
  run one at a time under a lock. `/api/clips` gives each clip's `reviewed`
  and `judgment`, so a reload restores saved spans and notes.
- **Only the page may write.** Any site open in the browser can POST to the
  loopback address. A POST needs `Content-Type: application/json` (which a
  cross-site page cannot send without a preflight this server never
  answers), an `Origin` of the page itself, and a `Host` of `127.0.0.1` or
  `localhost` with the port, against DNS rebinding. Anything else is `403`.
- **Layers**: the pure logic is `core/judging.py`; files go through a
  `ReviewFiles` Protocol (`JsonReviewFiles` wraps the existing label and
  manifest writers), built by `AppContext.review_files`; `review/store.py`
  holds the run's state. `review/` still imports no adapter.
- **Checked by the assistant** (2026-11-09, on a copy of
  `data/clips/v2917872953` with labels in a scratch folder, no browser): a
  fix, a post and a trash written as expected, the trashed file deleted,
  Save on it `409`, a POST with another Origin `403`; a restart restored all
  three judgments and the typed title; `eval` with `CLIPPER_LABELS_DIR` on
  the scratch picks folder read the file: 2 keepers (1 as cut, 1 ending
  2.5 s early), 1 rejected, 2 unjudged. The page is checked by manual step
  RV7.

## Amended by the re-render PR (V5 item 3d)

- **Save re-renders when the video changed**: the span differs from what
  the file covers (to the millisecond), or there is no file (trashed, or
  its render failed). A title or notes change alone does not re-render;
  titles are not burned in.
- **Same render path** as `render` and `drafts` (0007): the `ClipRenderer`
  from `AppContext.renderer`, the folder's `vertical` and `captions`,
  captions from the analysis transcript through `build_ass`, the draft's
  score and reasons. The file is named from the new span (0008), rendered
  to `<name>.part.mp4`, renamed into place, and the old file deleted if its
  name differs. A new name that is another clip's file is `409`, before
  anything is written.
- **The label is written first and stands** (decision 5). A failed render
  leaves a clip with a file its old file and span; a clip without a file
  gets the error in the manifest. The reply is `200` with `render_error`;
  an old file that could not be deleted is a `warning`.
- **The draft's span survives a re-render** in the manifest's
  `draft_start_seconds`/`draft_end_seconds` (0019 amendment), so the clip's
  own judgment, post or fix, and Reset still use it; `start`/`end` stay
  what the file covers. `/api/clips` gives `draft_start`/`draft_end`.
- **Save on a trashed clip re-renders it**, replacing 3c's refusal; a
  mistaken Trash is undone by Save.
- **`review` loads the analysis the manifest names** when the folder has
  captions, for its transcript, and checks its source; missing or for
  another video is `Error:`, exit 1, before serving, not a failure on the
  first Save. A folder without captions needs none.
- Writes stay one at a time, so a Save waits behind another's render; the
  page shows `Rendering…` and keeps playing.
- **Measured** (2026-11-09, by the assistant, on a copy of
  `data/clips/v2917872953`, no browser): a fix re-rendered in 11.8 s (a
  45.5 s 1080x1920 clip with word captions, checked with ffprobe), a
  trashed clip brought back in 11.1 s, a title-only Save 0.02 s; the old
  file deleted and the draft span kept. The page is checked by manual step
  RV8.

## Amended by the model title fix (after V5 item 3d)

- **Reset puts back the draft's span and the model's title**; notes stay,
  and nothing is written until Save. Found in RV8: Reset left an edited
  title, and the model's title was lost on the first edit.
- The page's first write to a clip keeps the model's title in the
  manifest's `model_title` (0019 amendment); `/api/clips` sends
  `model_title`, the current title until the page has written the clip.

## Amended by the Add clip PR (V5 item 3e)

- **Verdict `added`, not `post`** (decision 7): 0012's item 2 amendment
  defined `added` for a moment no draft proposed, and `eval` counts it as a
  keeper. Added clips reach anywhere in the VOD (whole-VOD amendment), not
  only the current range.
- **In the page**: "Add clip here" makes a new clip in the list from the
  playhead, 30 s long, clamped to the video. Until Save it lives only in
  the page; Trash discards it. It has no model title.
- **Save** (`POST /api/clips/add`, `{start, end, title, note}`, the same
  checks and cross-site rules as Save) writes an `added` label, renders the
  clip like any Save, and appends a manifest entry ranked after every clip
  so far, with score 0, the reason `added in review` and `added: true`
  (0019 amendment). Saving it again (`/api/clips/<rank>/save`) keeps
  `added` and moves its span. An overlap or a taken file name writes
  nothing; a failed render keeps the label and records the error.
- **Its own label is found by its last saved span**, kept in the draft span
  fields, since it has no detector span.
- **Trash removes an added clip outright**: its label, its file and its
  manifest entry, answering `{"removed": <rank>}`. A rejected span means a
  draft judged and dropped; an addition taken back was never a draft, and
  a rejected span would count candidates there as bad picks.
- **Checked by the assistant** (2026-11-09, on a copy of
  `data/clips/v2917872953`, no browser): an add at 1:23:20 rendered in
  8.4 s as rank 6 with an `added` label; Trash removed the file, the label
  and the entry, with no rejected span. The page is checked by manual step
  RV9.

## Amended by the frame URL fix (V5 item 4, while judging)

- **Frame URLs name the video**: `/frames/<key>/<second>.jpg`, where the
  key is the video's cache folder name (`<stem>-<size in bytes>`), sent as
  `frames_key` in `/api/clips`. Any other key is `404`.
- **Why**: every `review` run is served from the same address, and the
  browser keeps frames by URL for a day (film strip amendment), so the
  strip for one VOD showed frames cached from another (jasontheween and
  stableronaldo frames in ohnepixel's strip). The server's own cache was
  per video and correct; only the browser's mixed them. The player and the
  saved times were never affected.

## Amended by the start offset fix (V5 item 4, while judging)

- **`review` refuses a video whose first timestamp is past 0.1 s**
  (`MAX_START_SECONDS`), with `Error:` naming the start and the lossless
  ffmpeg remux that fixes it (`-map 0:v:0 -map 0:a:0 -c copy -movflags
  +faststart`). The start comes from the probe (0009 amendment).
- **Why**: yt-dlp saved one Twitch VOD (v2924046310) as a fragmented MP4
  whose timestamps start at 64 s. ffmpeg counts from the file's start, so
  its transcript, renders and frames agreed; the browser's player counted
  from 64 s, so the page showed footage 64 s before each draft and saved
  times 64 s late. Found from the maintainer's trims (three ends moved
  +64.1 to +66.2 s) and notes, which matched the speech 64 s earlier.
  Fragmented without an index, it also loaded and seeked slowly.
- **Repair of that VOD**: the remux above (45 s; same frame at 2:00:00 in
  both files), and every label and rejected span in its judged labels
  moved by -64 s, keeping verdicts, notes and `draft` spans; the original
  file and labels are kept outside the repo. Its drafts the maintainer did
  not trim were judged on the minute before them, so they count as
  unjudged in `eval`.
- Refusing rather than correcting the offset in the page, because how
  browsers treat a non-zero start was not verified beyond this one file.

## Amended by the style switch (ADR 0025, V5 item 13e)

- **Draft player and style switch**: the page shows the clip's rendered
  draft beside the trim strip, served at `/clips/<file>` for names the
  manifest lists only (404 otherwise), with byte ranges and `no-store`. A
  clip with styles gets a button per style; the pick shows as unsaved.
- **Save keeps one style**: the body may carry `style` (`fit` or `face`; any
  other value is 400, a style the clip lacks is 409). No style keeps the
  clip's own, its first variant. Without a re-render, `file` becomes that
  style's draft; with one, the new span is rendered in it, a `face`
  re-render tracking faces in the review process (`AppContext.face_tracker`
  and the probed frame size); no face in the new span is a render error and
  the old files stay. Either way the clip's other files are deleted and its
  `variants` hold the kept style alone. The judged label records `style`.
- Checked through the API on 13d's two podcast drafts: keeping `face` on an
  unchanged span switched `file` and deleted the fit file; a moved span kept
  as `face` re-rendered with face tracking in 18 s. The page script parses
  (esprima); the page itself is checked by manual step RV10, since the
  browser extension was not connected.

## Related

- 0011: render's failure policy
- 0012: evaluation, and judged labels (V5 amendment)
- 0019: the folder per video and its manifest
- 0022: the drafts command
- 0023: candidate boundaries
