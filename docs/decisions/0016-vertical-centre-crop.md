# 0016: Vertical 9:16 clips by a centre crop

- Status: Accepted
- Date: 2026-11-02
- Amends: 0007 (the filter chain), 0011 (a new `render` flag)
- Amended: the centre crop was replaced by fitting the whole frame over a
  blurred fill before release (see "Amended by the fit fix"). The title and
  decision 2 describe the original crop.

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

## Amended by the vertical crop PR (V4 item 2)

- `ClipRenderer.render` takes a keyword-only `vertical: bool = False`. The
  filter is the module constant `_VERTICAL_FILTER` in
  `adapters/rendering/ffmpeg.py`:
  `crop='trunc(min(iw,ih*9/16)/2)*2':'trunc(min(ih,iw*16/9)/2)*2',scale=1080:1920,setsar=1`.
  The quotes keep the commas inside `min()` away from the filtergraph parser.
  `crop` centres by default. The scaler is ffmpeg's default; `setsar=1` is set
  so players do not stretch the result.
- With `--captions`, there is one `-vf` value: the crop chain, then the
  `subtitles` filter, joined by a comma.
- A successful line ends with ` (vertical)`, or ` (vertical, captions)`.
- Checked on 2026-11-02 with ffmpeg 9.0.2 (gyan.dev full build), Windows 11,
  manual steps R15 to R17: every clip was 1080x1920 with SAR 1:1, durations
  0.025 to 0.028 s over the requested length on the real video, and the
  600x1280 constructed source was cropped in height without failing.
- Observed: `test_input.mp4` has black bars built into its 1280x720 frame.
  The crop keeps the full height, so the bars stay at the top and bottom of
  the vertical clip. Detecting and removing them (for example with
  `cropdetect`) is parked, not part of this ADR.
- The v0.2 caption style draws large on the vertical frame and wrapped to
  three lines in R16. Item 4 (0017) restyles captions.

## Amended by the fit fix (after V4 item 2)

The maintainer looked at the R15 frames and rejected the crop: on a 16:9
source it keeps about a third of the width, and `test_input.mp4`'s own
burned-in subtitle lost both ends. Nothing had been released with it.

- **Decision 2 is replaced.** `--vertical` now fits the whole frame inside
  1080x1920 (1080x608 for 16:9), at even sizes, centred on a copy of the same
  video that is scaled to fill 1080x1920, cropped to it, and blurred. Nothing
  of the source is cut off, and nothing is stretched.
- Filter, `_VERTICAL_FILTER` in `adapters/rendering/ffmpeg.py`:
  `split`; the background scaled to fill 270x480, cropped, `boxblur=10:2`,
  scaled to 1080x1920; the foreground scaled to fit 1080x1920 with
  `force_original_aspect_ratio=decrease:force_divisible_by=2`; `overlay`
  centred; `setsar=1`. The blur runs at quarter size because that is far
  cheaper than at full size. Its strength is a first guess, checked by eye.
- Still one `-vf` value and one re-encode path (0007); captions come after
  the overlay, so they draw over the whole 1080x1920 frame. With the v0.2
  style they land in the fill below the picture.
- The centre crop is not kept as an option. It may come back with subject
  tracking, which is still a V4 non-goal.
- Checked on 2026-11-02, same build and steps as above: every clip 1080x1920,
  SAR 1:1, the same durations; ranks 1 and 5 took 8.2 s (8.53 s with the
  crop). The 600x1280 source fitted by height with blurred strips at the
  sides.
- Letterbox bars built into a source now show inside the fitted picture, and
  blurred in the fill. Removing them stays parked.
- Alternatives considered here: black bars instead of the blur (simpler, but
  dead space), and a partial zoom to about 1:1 (keeps more than the crop but
  still cuts the edges). The maintainer chose the blurred fill.

## Amended by the face style (ADR 0025)

- `--style fit|face` selects the vertical style; `fit` is this ADR's (the
  fit fix) and stays the default. `face` is a moving full-height crop on the
  largest face, decided in ADR 0025. Decision 3 holds for both: one
  re-encode, the vertical frame first, then captions.

## Amended by zooms (V7 item 9h, ADR 0031)

- A clip may carry punch-in zooms, made in the review page: a stretch in
  source seconds (like cuts, so it stays on the moment), a scale of 1.1
  to 2.0 (default 1.3; past 2.0 1080p source upscaled goes soft), a
  centre `x`, `y` as fractions of the frame, and a cut-in or a `smooth`
  0.3 s ease in and out (half a short zoom). At most 10, in order and
  apart, inside the span; `Zoom` on the manifest clip and judged label.
- Rendered on the 9:16 frame after it is made (fit, face or a hand crop)
  and before captions, so captions and texts never zoom: `scale` with
  `eval=frame` to `1080*Z` by `1920*Z`, then `crop=1080:1920` at
  `1080*(Z-1)*x`, `1920*(Z-1)*y`. The crop offsets are expressions of
  `t`, not `iw`, which `crop` reads once (checked on ffmpeg 9.0.2: with
  `iw` the zoom stuck to the left edge). Zooms do not overlap, so each sum
  has one term at a time. Times move to the cut clip through the time
  map; a zoom inside a cut is left out.
- Vertical folders only; a Save with zooms in a landscape folder is
  refused (409). Without zooms the filter is unchanged.
- Measured on v2924392099, 8 s rendered vertical: 2.4 s plain, 2.8 s with
  a smooth zoom and a hand crop. Manual step RV23.

## Revisit when

- Real clips lose their subject often enough to matter. That is the case for
  tracking, and its own ADR.
- Upscaled 720p clips look visibly worse on a platform than native size.

## Related

- 0007: Cut clips by re-encoding
- 0011: `render` command shape and failure policy
- 0017: Word timestamps and styled captions
- 0019: A folder per video
