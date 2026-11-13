# 0007: Cut clips by re-encoding

- Status: Accepted
- Date: 2026-10-30
- Amended by: 0016 (the vertical crop filter)

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

## Amended by clip audio (V7 item 9i, ADR 0031)

- A clip may carry audio edits, made in the review page: its own volume
  (-12 to +12 dB), a music bed (a file from `data/music/`,
  `CLIPPER_MUSIC_DIR`, at -30 to 0 dB, default -18, looped and faded out
  over the last second, lowered under speech by default) and up to 10
  sound effects (files from `data/sfx/`, `CLIPPER_SFX_DIR`, at a moment
  of the source, -20 to +6 dB). The maintainer adds the files, which
  must be ones they may post; nothing is downloaded or bought. Both
  folders are under `data/`, so not committed. `ClipAudio` on the
  manifest clip and judged label.
- Still one encode: the music (`-stream_loop -1`) and effects are extra
  inputs after the video, before the output length, and a
  `-filter_complex` mixes the audio while `-vf` keeps the video (checked
  on ffmpeg 9.0.2): the clip's sound through the cut filter and
  `volume`, the music through `volume`, `afade` and a `sidechaincompress`
  keyed on the clip's sound, each effect through `adelay` and `volume`,
  then `amix` with `normalize=0` as long as the clip. Effect times move
  to the cut clip; one inside a cut is left out. Without audio edits the
  command is unchanged.
- Ducking set on v2924392099 with a -18 dB bed under 8 s of talk:
  threshold 0.05 and ratio 8 lowered the music 2.6 dB (mean -39.5 to
  -42.1), 0.01 and 10 by 11.6 dB (to -51.1), 0.005 and 20 by 19 dB;
  0.01 and 10 kept, as constants in the renderer to change by ear. A
  10 s clip with a cut, gain, ducked music and an effect rendered in
  2.5 s.
- A mix needs the source's audio stream: a source without one fails
  with ffmpeg's "matches no streams" (the design said music without
  ducking; the probe does not report audio streams). Every VOD tried
  has audio. Save refuses a music or effect file not in its folder
  (409). Manual step RV24.

## Revisit when

- Render time on long clips becomes a problem. Stream copy becomes an opt-in
  flag then.
- The manual check shows cuts are not accurate enough on a supported build.

## Related

- 0004: Report ffmpeg's actual error instead of the stderr tail
- 0008: Output location and naming
- 0010: Burn in captions from segment-level text
