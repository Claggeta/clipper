# 0005: Classify "no audio stream" in the ffmpeg extractor

- Status: Accepted
- Date: 2026-10-30
- Amends: 0004 (the "adapter stays agnostic" clause, for this one condition)

## Context

A video with no audio stream makes ffmpeg exit with `Output file does not
contain any stream`. The CLI showed the generic `ffmpeg failed (exit N): ...`
message, which says neither what is wrong nor how to fix it. 0004 deferred this
case to its own design discussion.

## Decision

1. `FfmpegAudioExtractor` matches `does not contain any stream`
   (case-insensitive) in stderr and raises `NoAudioStreamError`, a subclass of
   `AudioExtractionError`, so the CLI's `except ClipperError` still catches it.
   The message names the file and the remedy. Any other failure still raises
   the generic `AudioExtractionError`. The full stderr still goes to
   `logger.info` first.
2. The command uses `-map 0:a:0?` (first audio stream, optional) in place of
   `-vn`. `-map 0:a?` was rejected: it maps every audio stream, and the WAV
   muxer fails on files with more than one (verified on ffmpeg 9.0.2).

## Alternatives considered

- **Match in `VideoTranscriber`.** Rejected. It would parse a message the
  extractor already formatted, and put ffmpeg knowledge in a class that only
  knows the `AudioExtractor` Protocol.
- **`ffprobe` pre-check.** Rejected for now. It adds a second binary invocation
  and a probe adapter for a single condition.

## Consequences

- (+) The user sees the cause and the remedy.
- (+) A future ffmpeg wording change degrades to the generic error, not a
  crash.
- (−) The extractor now depends on one line of ffmpeg's wording.
- (−) The tests mock `subprocess.run`, so they cannot see real ffmpeg output.
  The no-audio case belongs on the manual test checklist.

## Revisit when

- A second failure mode needs classifying. Build the `ffprobe` probe then,
  instead of adding more string matches.

## Related

- 0004: Report ffmpeg's actual error instead of the stderr tail
