# 0009: `render` reads the saved JSON and probes the media

- Status: Accepted
- Date: 2026-10-30

## Context

`clipper render` needs candidates and, for captions, the transcript. Both are
already in the `AnalysisResult` JSON that `analyze` saves. That JSON records
`source` as the video's file name only, with no path or fingerprint, so it can
be paired with the wrong video. A candidate beyond the end of a video does not
make ffmpeg fail with a useful message, and 0005 said to build an `ffprobe`
probe when a second failure mode needed classifying rather than adding string
matches.

## Decision

1. `clipper render <video> <result.json>` loads an `AnalysisResult` with
   `model_validate_json`. Invalid or unreadable JSON is a `ClipperError`.
2. It fails if the result's `source` differs from the video's file name.
3. A `MediaProbe` Protocol in `core/` returns structured facts (duration in
   seconds, whether there is a video stream). `FfprobeMediaProbe` in
   `adapters/media/` implements it by running `ffprobe` with JSON output. It
   does not match stderr text for classification.
4. `render` fails before cutting anything if any candidate ends after the
   probed duration, or if the file has no video stream.

## Alternatives considered

- **Re-run analysis inside `render`.** Rejected. The cache makes it cheap, but
  it couples the commands and ties `render` to Whisper settings.
- **Add a fingerprint (size, mtime or hash) to `AnalysisResult`.** Rejected for
  now. It changes the V1 JSON schema, and existing files would lack it.
- **No checks.** Rejected. A mismatched JSON would render the wrong spans
  without any error.
- **Match ffmpeg stderr for these cases.** Rejected, as in 0005.

## Consequences

- (+) Mismatches and out-of-range candidates fail early with a clear message,
  and nothing partial is written.
- (+) The probe reports structure, so it does not depend on ffmpeg's wording.
- (−) The name check is weak: a different video with the same file name
  passes. The duration check catches some of those, not all.
- (−) `ffprobe` is a second binary that must be on `PATH`. It normally ships
  with ffmpeg, but a missing one needs its own clear error.
- (−) Neither CI nor the mocked tests see real `ffprobe` output. Parsing its
  JSON belongs on the manual checklist.
- (−) The stderr-formatting and `which` logic is shared with the extractor
  and renderer (V2 item 2), so the probe reuses it rather than adding a copy.

## Revisit when

- A mismatch between JSON and video gets past the checks in practice. Then
  add a fingerprint to `AnalysisResult`.

## Related

- 0004: Report ffmpeg's actual error instead of the stderr tail
- 0005: Classify "no audio stream" in the ffmpeg extractor
