# 0006: A private factory is the CLI's test seam for the transcriber

- Status: Accepted
- Date: 2026-10-30

## Context

`analyze()` built `FasterWhisperTranscriber`, `VideoTranscriber` and
`CachingTranscriber` inline, so a CLI test could not run the success path
without a Whisper model and ffmpeg. `cli/` is the composition root and the only
layer allowed to pick concrete adapter classes, so the seam had to live there.

## Decision

`cli/main.py` composes the transcriber in `_build_transcriber(settings)`, and
`analyze()` calls it. Tests replace it with
`monkeypatch.setattr(cli_main, "_build_transcriber", ...)` and return a
`FakeTranscriber`. Output location is redirected with `CLIPPER_OUTPUT_DIR`.

## Alternatives considered

- **Typer context object (`ctx.obj` with injected dependencies).** Rejected for
  now. About 25 lines of plumbing and a public-looking extension point for one
  command.
- **Settings/env switch (`CLIPPER_TRANSCRIBER=fake`).** Rejected. It puts a test
  double on the production path, lets a user silently get fixed sample output
  for any video, and `Settings` (in `core/`) cannot name adapters, so `cli/`
  would branch on a string anyway.

## Consequences

- (+) Small change, production behaviour unchanged, layering intact.
- (−) Tests depend on a private name; a rename fails loudly.
- (−) Tests that replace the factory do not exercise the `CachingTranscriber`
  and `VideoTranscriber` wiring. Those have their own unit tests.

## Revisit when

- A second command needs injected dependencies. Move to the context-object
  approach then.
