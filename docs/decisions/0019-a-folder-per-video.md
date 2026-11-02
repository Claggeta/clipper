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

## Revisit when

- The maintainer wants a run's earlier clips kept in the manifest.
- Same-stem collisions happen in practice. Videos named by ID, as they are
  downloaded today, make them unlikely.

## Related

- 0008: Clip output location and naming
- 0016: Vertical 9:16 clips by a centre crop
- 0018: Suggested title and hook per clip
