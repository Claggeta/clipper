# 0011: `render` command shape, test seams and failure policy

- Status: Accepted
- Date: 2026-10-30
- Amends: 0006 (the "revisit when" trigger)

## Context

`cli/` is the composition root. 0006 gave `analyze` one private factory,
`_build_transcriber`, and said to move to a context object when a second
command needed injected dependencies. `render` is that second command, and it
needs a renderer and a probe. A render run may also produce several clips, and
one can fail while others succeed.

## Decision

1. `clipper render <video> <result.json>` is a separate command. It renders
   every candidate in the result by default, and `--rank N` (repeatable) limits
   it to chosen ranks, where rank is the 1-based position in the saved
   candidate list. Captions are `--captions`.
2. `cli/main.py` adds `_build_renderer(settings)` and `_build_probe()`, patched
   by tests the same way as `_build_transcriber`.
3. If one clip fails, the rest are still rendered. The command then prints
   each failure and exits 1. Errors found before any cutting (bad JSON, name
   mismatch, out-of-range candidate, no video stream) exit 1 with nothing
   written.

## Alternatives considered

- **`analyze --render`.** Rejected. It couples two stages in one command and
  makes `analyze` do more than it says.
- **Typer context object for injection (0006's plan).** Rejected for two
  commands. About 25 lines of plumbing for three seams.
- **Stop at the first failure.** Rejected. One bad clip would discard the
  rest of the run, and clips are independent.

## Consequences

- (+) The pattern is the one already in use and understood.
- (+) Each stage stays a command that does one thing.
- (−) Tests depend on three private names. A rename fails loudly.
- (−) Tests that patch the factories do not exercise the real wiring. The
  adapters have their own tests, and the manual checklist covers the real run.
- (−) `--rank` refers to positions in the JSON, which is score order at the time
  `analyze` ran. A hand-edited JSON changes what a rank means.

## Amended by the `render` command PR (V2 item 7)

- Check order, all exit 1 with nothing written: load the JSON, source name,
  rank selection, probe and video stream, duration, `plan_clip_paths`. Cheap
  checks run before the `ffprobe` subprocess.
- `--rank` values are validated by `render` and raise `ClipRenderError`, not
  Typer's usage error, so `0` and `99` both exit 1. Duplicates are dropped and
  clips render in ascending rank order, whatever order the flags were given.
- The duration check (0009) and the collision check (0008) cover the selected
  candidates only. A bad or colliding candidate that was not asked for does not
  block a `--rank` run.
- A result with no candidates exits 1 ("no candidates to render").
- The duration check is strict: a candidate ending even slightly past the
  probed duration fails. If real runs show Whisper segment ends overshooting
  the container duration, add a small tolerance and amend 0009.
- Output is plain lines, not a table: one `Rendered #N <span> <path>` per
  success as it completes, then each failure, then
  `Rendered X of Y clips to <dir>`. Messages are escaped for rich markup.
- `render` has no `-v`, so the full ffmpeg stderr INFO log (0004) is not
  reachable from the CLI yet.

## Revisit when

- A third command or a third injected dependency appears. Move to the context
  object then, as 0006 describes.

## Related

- 0006: A private factory is the CLI's test seam for the transcriber
- 0009: `render` reads the saved JSON and probes the media
