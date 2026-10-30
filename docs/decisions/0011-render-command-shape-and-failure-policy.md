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

## Revisit when

- A third command or a third injected dependency appears. Move to the context
  object then, as 0006 describes.

## Related

- 0006: A private factory is the CLI's test seam for the transcriber
- 0009: `render` reads the saved JSON and probes the media
