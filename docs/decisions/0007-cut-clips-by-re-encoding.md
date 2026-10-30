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

## Revisit when

- Render time on long clips becomes a problem. Stream copy becomes an opt-in
  flag then.
- The manual check shows cuts are not accurate enough on a supported build.

## Related

- 0004: Report ffmpeg's actual error instead of the stderr tail
- 0008: Output location and naming
- 0010: Burn in captions from segment-level text
