# 0008: Clip output location and naming

- Status: Accepted
- Date: 2026-10-30
- Clarified: where the within-run collision check lives (see Consequences)

## Context

`clipper render` writes one file per candidate. Candidates are ranked by score,
so rank can change between runs, detector versions and `top_n` values. The V1
JSON is already named after the video stem and overwrites on collision.

## Decision

1. Clips are written to `data/clips/`, set by `CLIPPER_CLIPS_DIR` (a new
   `Settings.clips_dir`, relative paths resolving against the current
   directory like the other settings).
2. The name is `<stem>_<start>-<end>.mp4`, where `<start>` and `<end>` are
   zero-padded `HHMMSS`. Start is floored and end is ceiled to whole seconds,
   so the name never claims a shorter range than was cut. Example:
   `talk_000032-000113.mp4`. The cut itself uses the exact float times.
3. An existing file with the same name is overwritten (`-y`).

## Alternatives considered

- **Rank in the name (`<stem>_01.mp4`).** Rejected. Readable, but the same
  clip gets a different name when the ranking changes.
- **Fail if the file exists.** Rejected. Re-running `render` should be
  idempotent, and the JSON already overwrites.
- **Auto-suffix on collision.** Rejected. Re-runs would litter the folder.
- **Put clips next to the JSON in `data/output/`.** Rejected. It mixes
  analysis results with rendered media and makes cleaning one unsafe.

## Consequences

- (+) The same candidate always maps to the same file name.
- (+) The name is self-describing without opening the JSON.
- (−) Two videos with the same stem and overlapping ranges overwrite each
  other. This is the V1 limitation, now covering clips. It goes in the README's
  known limitations at release.
- (−) Stale clips remain if a later run produces different candidates. Nothing
  deletes old files.
- (−) Two candidates that round to the same name would collide. The detector
  deduplicates overlapping windows, so this should not occur, but the renderer
  should fail loudly rather than silently overwrite within one run.
  Clarification: the check is `plan_clip_paths` in `core/naming.py`, which the
  `render` command calls once before cutting anything. Renderers are stateless
  and take the output path from the caller, so the run fails with nothing
  written. Times are rounded to milliseconds before flooring and ceiling, so
  float noise cannot change a name.

## Revisit when

- Collisions across same-named videos cause real trouble. The fix is probably
  a per-video subfolder, which also lets the JSON name collision be fixed.

## Related

- 0007: Cut clips by re-encoding
