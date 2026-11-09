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

## Related

- 0011: render's failure policy
- 0012: evaluation, and judged labels (V5 amendment)
- 0019: the folder per video and its manifest
- 0022: the drafts command
- 0023: candidate boundaries
