# 0019: A folder per video, with a manifest

- Status: Accepted
- Date: 2026-11-02
- Amends: 0008 (decision 1, through its "revisit when")

## Context

0008 puts every clip in one folder, `data/clips/`. With 4 to 10 long videos a
week and a review step where the maintainer picks what to post, one flat folder
mixes videos. Titles (0018) and the flags a clip was rendered with need a place
to live next to the clips. 0008's "revisit when" already names a per-video
subfolder as the likely fix.

## Decision

1. `render` writes clips to `<clips dir>/<stem>/`, where `<stem>` is the
   video's file name without its extension. File names inside are unchanged
   (`<stem>_<HHMMSS>-<HHMMSS>.mp4`, 0008), so a clip moved out of its folder
   still names its video.
2. Each run writes `manifest.json` in that folder, listing every clip the run
   rendered: rank, span, score, reasons, path, the flags (`captions`,
   `vertical`, `titles`) and, with `--titles`, the title and hook. A pydantic
   model defines it. Failed clips are listed with their error.
3. The manifest is replaced on each run, like the clips (0008). A `--rank` run
   therefore lists only the ranks it rendered.
4. This always applies, not only in the one-command flow: one code path.

## Alternatives considered

- **Keep the flat folder and add a manifest per video** (`<stem>.json`).
  Fewer changes, but clips from many videos still mix. Rejected.
- **Merge the manifest across runs.** Keeps earlier ranks, but has to decide
  what a stale entry means. Rejected for now.
- **Drop the stem from file names inside the folder.** Shorter names, but a
  clip moved out loses its origin. Rejected.

## Consequences

- (+) One folder per video to review, with titles beside the clips.
- (+) Fixes 0008's cross-video clip collision for videos with different stems.
- (-) Changes v0.2's output path. The manual steps that list `data\clips`
  change in the implementing PR.
- (-) Two videos with the same stem still share a folder and overwrite each
  other, as in 0008.
- (-) Clips from older runs stay in the old flat folder. Nothing moves them.

## Amended by the folder and manifest PR (V4 item 4a)

- `clip_folder(video, clips_dir)` in `core/naming.py` gives
  `<clips dir>/<stem>`. `render` passes it to `plan_clip_paths`, so the
  within-run collision check (0008) is unchanged.
- **Manifest model.** `RenderManifest` and `ManifestClip` in
  `domain/models.py`, written by `save_manifest` in
  `adapters/results/manifest_json.py` as indented UTF-8 with LF endings.
  - Run level: `source` (the video's file name), `analysis` (the result JSON
    path as given on the command line), `detector` (copied from the result,
    `null` for older JSON), and the flags `vertical` and `captions`.
  - Per clip: `rank`, `start_seconds`, `end_seconds`, `score`, `reasons`,
    `file`, `captions` and `error`. Exactly one of `file` and `error` is set.
- **Changes from decision 2:**
  - `file` is the name inside the folder, not a path, so the folder can be
    moved.
  - The flags are recorded once per run, since they are per run. Per clip,
    `captions` records what was burned in: `words`, `segments` (a transcript
    without word times, 0017) or `null` (not asked for, or no speech in
    range).
  - There is no `titles` flag, title or hook yet. Item 5 (0018) adds them.
  - No timestamp. The file's modified time gives one, and the output stays
    the same for the same run.
- **When it is written.** Once, after every selected clip has been tried,
  including when some or all failed; the exit code is still 1 then (0011).
  `error` holds the raw message, not the console's escaped one. A failure
  before cutting writes nothing, so the previous manifest stays. If the
  manifest cannot be written, `render` prints `Error:` and exits 1, and the
  clips it rendered stay.
- `render` prints `Rendered X of Y clips to <folder>` and then
  `Manifest: <path>`.

Checked on 2026-11-02 with ffmpeg 9.0.2 (gyan.dev full build), Windows 11, by
the assistant in a separate process: the Rendering and Captions sections of
`docs/manual-testing.md` run as written on `test_input.mp4`, including the new
step R18. Rendering all 5 candidates took 11.6 s. The manifest listed the
5 clips in rank order with `captions` `words`, a `--rank 2` run replaced it
with one entry, the constructed failure from R14 was listed with ffmpeg's
`Permission denied` message, and a rank error left it unchanged. The v0.3
analysis of the same video gave `captions` `segments` and `detector` `null`.

## Amended by the review page's Save PR (V5 item 3c)

- **`reviewed`** per clip: `"saved"`, `"trashed"` or absent, written by the
  review page (0024). Older manifests read as absent.
- **A trashed clip has no `file`**: Trash deleted it. It needs no `error`,
  and keeps one it had. Every other clip still has exactly one of `file` and
  `error`.
- **The title** may be changed or cleared in the page (0018 amendment).
- **Start and end are unchanged by Save** in this item: the file still
  covers the draft's span until a re-render (V5 item 3d). The saved span is
  in the judged label.
- **Re-render amendment (V5 item 3d)**: a Save that re-renders moves
  `start_seconds`/`end_seconds` and `file` to the new span, and sets
  `draft_start_seconds`/`draft_end_seconds` (together, or neither) to the
  detector's span, which the review page finds judgments by. Absent means
  the clip was never re-rendered.
- **Model title amendment (after V5 item 3d)**: `model_title` keeps the
  title `render --titles` wrote, set by the page's first write to the clip
  (Save or Trash) and never changed after; None if the model wrote none.
  The page's Reset puts it back, and V5 item 8b compares it with `title`.
  Manifests the page wrote before this have none.
- **Add clip amendment (V5 item 3e)**: a clip added in the review page has
  `added: true`, a rank after every clip so far, score 0 and the reason
  `added in review`; its draft span fields hold its last saved span. Trash
  removes its entry. Older manifests read as `added: false`.
- **Styles amendment (ADR 0025, V5 item 13d)**: the run records `styles`,
  the vertical styles rendered in order (empty for a landscape run), and each
  clip `variants`, a list of `{style, file}`; `file` is the first variant's.
  Older manifests read as empty. The review page's re-render and Trash clear
  `variants` until the style switch (13e).
- **Style switch amendment (V5 13e)**: a Save in the page keeps one style;
  `file` is its draft and `variants` holds it alone. Trash still clears
  them.
- The page replaces `manifest.json` whole, as `render` does. A later
  `render` or `drafts` run on the folder still replaces it and loses these
  fields (decision 3); the judged labels are separate files.

## Amended by the channel and day folders PR (V6 item 4d)

The maintainer asked for drafts filed by channel and day, to find them
when posting.

- **Where**: `<clips dir>/<channel>/<MM-DD-YYYY>/<stem>/`. The channel is
  the info file's (0020 video info amendment), as the platform spells it,
  with the characters Windows refuses in a folder name dropped; no info
  file, or a bad one, files the drafts under `Local`. The day is the day
  the run made the folder (the maintainer's choice over the stream date),
  in US order with dashes, since `/` cannot be in a folder name.
- **One folder per video**: a run reuses the video's existing folder,
  found by stem anywhere under the clips dir, flat or nested, so a re-run
  on another day replaces that folder's manifest as before (decision 3)
  instead of starting a second folder. With two, the newest manifest's.
- **Inside the folder** nothing changes: the same file names and
  `manifest.json`.
- **Readers**: the queue (0028) finds manifests at any depth and writes
  each clip's full path; `posted` takes the full path or `<stem>/<file>`,
  and the post record keeps naming a clip `<stem>/<file>`. `review` takes
  the folder's path, as before.
- **Existing folders** were moved into the layout once, by hand, with a
  script that is not kept: channel from the info file, day from the
  folder's creation date.

## Revisit when

- The maintainer wants a run's earlier clips kept in the manifest.
- Same-stem collisions happen in practice. Videos named by ID, as they are
  downloaded today, make them unlikely.

## Related

- 0008: Clip output location and naming
- 0016: Vertical 9:16 clips by a centre crop
- 0018: Suggested title and hook per clip
