# 0010: Burn in captions from segment-level text

- Status: Accepted
- Date: 2026-10-30

## Context

The transcript is segment-level: `FasterWhisperTranscriber` calls
`transcribe(..., vad_filter=True)` without word timestamps, and the transcript
cache stores that shape. Clips are meant to be publishable, and most platforms
ignore soft subtitle tracks.

## Decision

1. A pure function in `core/` turns a `Transcript`, a clip window and nothing
   else into SRT text. Segments overlapping the window are kept and clamped to
   it, and times are shifted so the clip starts at zero. It does no I/O.
2. The renderer writes that text to a temporary `.srt` and burns it in with
   ffmpeg's `subtitles` filter, using one fixed style. The temporary folder is
   removed on success and on failure.
3. Captions are opt-in through a CLI flag.

## Alternatives considered

- **Soft subtitles (a `mov_text` track).** Rejected. They work with stream
  copy, but players and most platforms ignore them, so the clip is not
  publishable.
- **Sidecar `.srt` only.** Rejected for V2. It defers the problem and does not
  produce a finished clip.
- **Word-level captions.** Rejected as out of scope. They need
  `word_timestamps=True`, a `Word` domain model and a change to the cached
  transcript schema, with its own migration question.

## Consequences

- (+) The caption logic is pure and fully unit-testable without ffmpeg.
- (+) The result plays anywhere.
- (−) `base` segments can be long, so a caption block may be large and fill
  the frame.
- (−) Burn-in needs libass in the ffmpeg build. A build without it fails with
  ffmpeg's own message.
- (−) The `subtitles` filter treats `:` and `\` specially, so Windows paths
  such as `C:\Users\...` need escaping. A temp-directory path in a fixed place
  keeps this to one function, which needs a test and a manual step.
- (−) Segment boundaries cut mid-sentence at the clip edges, since text is
  clamped by time and not by words.

## Revisit when

- Segment-level captions look bad on real clips. That is the case for
  word-level timestamps and its own ADR.

## Related

- 0002: Use faster-whisper for transcription
- 0007: Cut clips by re-encoding
