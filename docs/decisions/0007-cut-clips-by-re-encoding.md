# 0007: Cut clips by re-encoding

- Status: Accepted
- Date: 2026-10-30

## Context

V2 renders each `ClipCandidate` to its own video file. Candidate start and end
times are arbitrary window edges chosen by the detector, not keyframe
positions. Burned-in captions (0010) are a video filter, and filters need
decoded frames.

## Decision

`FfmpegClipRenderer` always re-encodes:

- `-c:v libx264`, `-c:a aac`, `-pix_fmt yuv420p`
- `-ss` before `-i` (input seeking), with `-t` for the duration
- `-hide_banner -loglevel error`, so 0004 applies unchanged to render errors

There is one code path. The exact quality settings (crf, preset) are chosen in
the implementing PR and recorded in the code, not here.

## Alternatives considered

- **Stream copy (`-c copy`).** Rejected as the default. It is fast and lossless,
  but cuts snap to keyframes, so a clip can start early or open on a few
  frozen or broken frames, and its duration is inexact. It also cannot burn in
  captions.
- **Hybrid: copy without captions, re-encode with them.** Rejected. Two code
  paths, two sets of manual tests, and the no-caption path has the accuracy
  problem above.
- **Hardware encoders.** Out of scope for V2.

## Consequences

- (+) Cuts are intended to start and end at the requested times, and one code
  path serves clips with and without captions.
- (+) `yuv420p` and H.264/AAC play in common players regardless of the source
  codec. The reference clip in `docs/manual-testing.md` is AV1.
- (−) Re-encoding is slower than copying and lossy. No render time has been
  measured yet.
- (−) The ffmpeg build must include libx264. A minimal build may not, and the
  error would come from ffmpeg's stderr.
- (−) libx264 is GPL. Clipper does not ship ffmpeg, but the project's own
  licence is undecided (see `docs/vision.md`).
- (−) Frame accuracy is the intent, not something CI can confirm. The
  rendering section of `docs/manual-testing.md` measures actual start and end
  with `ffprobe`.

## Amended by the `FfmpegClipRenderer` PR (V2 item 6)

- Streams: `-map 0:v:0` (required) and `-map 0:a:0?` (first audio stream,
  optional). The first audio stream is the one the transcriber heard. A source
  with no audio renders a video-only clip instead of failing; analysis already
  requires audio (0005), so this only arises with a mismatched source. Explicit
  maps also keep subtitle and data streams out of the MP4.
- `-t` is an output option (after `-i`); `-ss` stays before `-i`. Times are
  passed as fixed-point milliseconds.
- Quality settings live in `adapters/rendering/ffmpeg.py`: `libx264`
  `-preset medium -crf 20`, `aac` at 128k, `+faststart`, `-nostdin`. They are
  not measured against real footage.
- On a non-zero exit the renderer deletes the output path, so a broken file is
  never left under a clip name. This also removes a previous good clip of the
  same name, which `-y` had already truncated.
- The renderer refuses an output path equal to the input, because `-y` would
  destroy the source.

## Revisit when

- Render time on long clips becomes a problem. Stream copy becomes an opt-in
  flag then.
- The manual check shows cuts are not accurate enough on a supported build.

## Related

- 0004: Report ffmpeg's actual error instead of the stderr tail
- 0008: Output location and naming
- 0010: Burn in captions from segment-level text
