# 0016: Vertical 9:16 clips by a centre crop

- Status: Accepted
- Date: 2026-11-02
- Amends: 0007 (the filter chain), 0011 (a new `render` flag)

## Context

Short-form platforms show clips as 9:16 vertical video. `render` writes clips
in the source frame, usually 16:9, which was a V2 non-goal. The maintainer
needs vertical clips before using the tool daily (V4 scope). Following the
speaker or subject needs a visual model; a fixed centre crop needs only
ffmpeg.

## Decision

1. `render --vertical` crops each clip to 9:16 and scales it to 1080x1920. It
   is off by default, so plain `render` keeps v0.2's output and the manual
   steps R1 to R14 stay valid. The one-command flow (V4 item 8) turns it on.
2. The crop is centred and computed by ffmpeg from the input size, so no extra
   probe is needed: width `min(iw, ih*9/16)` and height `min(ih, iw*16/9)`,
   each rounded down to an even number for `yuv420p`. A source that is already
   9:16 or narrower is cropped in height, not width, rather than failing.
3. It is a filter in the existing re-encode (0007), so there is still one code
   path. The order is crop, scale, then captions (0017), so captions are laid
   out on the vertical frame.
4. The scaler, and whether `setsar=1` is needed, are chosen in the
   implementing PR and recorded in the code.

## Alternatives considered

- **Vertical by default.** Rejected for now. It changes v0.2's output and
  every rendering manual step, and the one-command flow can turn it on.
- **Keep the native crop size** (405x720 from a 720p source). No upscaling,
  but the size varies with the source. Rejected in favour of one fixed size.
- **Blurred or padded background** (the 16:9 frame letterboxed on a blurred
  copy). Keeps the whole frame but leaves small content in the middle.
  Not chosen; it can be a later option.
- **Subject or face tracking.** A V4 non-goal. It needs a visual model.

## Consequences

- (+) One flag gives a clip in the platforms' shape, with no new dependency.
- (+) The crop needs no knowledge of the source size in Python.
- (-) A subject who is not in the middle of the frame is cut off (vision risk).
  Streams with a facecam in a corner are the likely case.
- (-) A 720p source is upscaled 1.5x to 1080 wide, which adds no detail.
- (-) A vertical clip has the same file name as a plain one of the same span
  (0008), so one overwrites the other, as with `--captions`.
- (-) The crop and scale are unverified on real footage until the manual step
  in the implementing PR.

## Revisit when

- Real clips lose their subject often enough to matter. That is the case for
  tracking, and its own ADR.
- Upscaled 720p clips look visibly worse on a platform than native size.

## Related

- 0007: Cut clips by re-encoding
- 0011: `render` command shape and failure policy
- 0017: Word timestamps and styled captions
- 0019: A folder per video
