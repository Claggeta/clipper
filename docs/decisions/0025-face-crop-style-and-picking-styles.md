# 0025: A face crop style, and picking a style per clip

- Status: Accepted
- Date: 2026-11-10
- Amends: 0016 (a second vertical style), 0019 (variants per clip in the
  manifest), 0024 (a style switch in the review page), and the V5 scope
  (face-aware cropping is built in V5)

## Context

`--vertical` fits the whole 16:9 frame over a blurred fill (0016). The
subject stays small in the middle. The maintainer asked for clips that zoom
in on whoever is talking, and for several styles per clip to pick from.
The V5 scope allowed face-aware cropping only as a spike that ends in an
ADR, built in V5 only if it picked the facecam split.

The spike (V5 item 13, 2026-11-10) ran OpenCV's YuNet face detector
(`face_detection_yunet_2023mar.onnx`, 232 KB, Apache-2.0, from
`opencv/opencv_zoo`) on 1,354 frames, one every 2 s, from the judged keepers
of five videos:

| Video | Layout | Face found | One subject dominates | Main face holds still (2 s) |
|---|---|---|---|---|
| 5pk8HLcAAT0 | podcast, framed speakers | 94% | 79% | 74% |
| v2924046310 (ohnepixel) | webcam, sometimes a small facecam | 99% | 98% | 83% |
| v2922589775 (adapt) | gameplay with a facecam, and IRL | 89% | 75% | 84% |
| v2922695332 (jasontheween) | IRL, groups | 96% | 67% | 55% |
| v2917872953 (stableronaldo) | IRL, two or more people | 90% | 50% | 50% |

"One subject dominates" is one face, or a largest face at least twice the
height of the next. "Holds still" is the largest face's centre moving under
5% of the frame width between samples of one clip. Detection took about
35 ms per 1280x720 frame on the CPU.

- Several faces rarely fit one full-height 9:16 window (607 of 1920 px):
  15 of 179 multi-face frames on stableronaldo, 12 of 81 on jasontheween.
- A prototype full-height crop that follows the largest face (5 samples a
  second, a dead zone, easing, ffmpeg `sendcmd` moving a `crop`) gave clean
  vertical framing of each speaker on the podcast. On stableronaldo in a car
  it sometimes framed the wrong person and cut the other off, and found no
  face in 38 of 211 samples. On an ohnepixel clip with a small facecam in a
  corner over dark content, it gave a tall strip with a tiny face: that
  layout needs a facecam split, not a crop.
- The layout changes within one VOD (gameplay, IRL, just chatting), so a
  style suits a clip, not a video.
- `opencv-python-headless` 5.0.0.93 resolves with the locked `numpy`
  2.5.4; 4.12 would pull `numpy` down to 2.2.6. Both found faces in the same
  54 of 60 podcast frames.

## Decision

1. **Two vertical styles.** `fit` is today's `--vertical` (0016's fit fix),
   and stays the default and the fallback. `face` is new: a full-height 9:16
   crop centred on the largest face, held inside a dead zone, eased within a
   shot, moved at once at a camera cut, and held where no face is found. A
   clip with no face at all is not rendered in `face`, with a note.
2. **Largest face stands for the speaker.** No speaker detection: matching
   voices to faces needs heavier models, the common ones behind a Hugging
   Face login. Revisit if the review page shows `face` framing the wrong
   person often.
3. **Faces are found once per clip** at 5 samples a second by a
   `FaceTracker` Protocol in `core/` with an OpenCV adapter, and cached under
   `<cache dir>/faces/`, so re-renders and other styles reuse them.
   `opencv-python-headless` 5.0.x is a new dependency; the model is
   downloaded once into the cache dir and checked against its SHA-256
   (`8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`).
4. **Several styles per clip, picked in the review page.** `drafts` renders
   every clip in `fit` and `face`. The manifest lists each clip's variants
   (0019); the review page shows a style switch, and Save keeps the chosen
   style and records it with the judged label, so picks show which style
   suits which videos.
5. **The facecam split is a later item**, with its own measurement: finding a
   fixed facecam box needs more than the spike's check, which found adapt's
   cam but also gave false hits.
6. **Built in V5, in PRs:** this ADR and the scope; face tracking and its
   cache; the `face` style in `render` with a manual step; several styles in
   `drafts` and the manifest; the style switch in the review page. Each code
   PR states its design and waits for confirmation.

## Amended by face tracking (V5 item 13b)

- **The model is kept in the package**, not downloaded:
  `src/clipper/adapters/media/models/face_detection_yunet_2023mar.onnx`
  (232 KB, SHA-256 as in decision 3, checked by a unit test) with its licence
  beside it as `LICENSE-yunet`. No download code, no network on first use,
  and it works offline. This replaces decision 3's download. The model's
  licence is MIT (Shiqi Yu), not Apache-2.0 as the context said.
- **`FaceTracker.track(video, start, end) -> FaceTrack`** in `core/`, with
  `FaceBox` (fractions of the frame), `FaceSample`, `FaceTrack` and
  `FaceError` in `domain/`.
- **`OpenCvFaceTracker`** (`adapters/media/faces.py`): ffmpeg pipes raw
  frames at 5 a second, scaled to 1280x720, straight into YuNet (score at
  least 0.8); nothing is written to disk. The 2023mar model has a fixed
  input size, which the fixed frame size meets; opencv_zoo's newer 2026may
  export has dynamic dims for OpenCV 5's engine, and was not measured. A
  source that is not 16:9 is squashed to fit. OpenCV's log level is set to
  errors, since OpenCV 5 warns on every model load.
- **`CachingFaceTracker`** keeps one JSON per span at `<cache
  dir>/faces/<stem>-<size>/yunet2023mar-5fps-1280x720-<start ms>-<end
  ms>.json`. On the podcast clip 11:30 to 12:30: 299 samples, a face in
  each, 5.9 s, then 0.0 s from the cache.
- **`AppContext.face_tracker`** builds it; nothing calls it until 13c.
- An integration test loads the bundled model in the installed OpenCV and
  runs it on a blank frame (no face), on every push.

## Amended by the face style in `render` (V5 item 13c)

- **`render --vertical --style face`**; `--style` is `fit` by default, so
  `--vertical` alone is unchanged. `--style face` without `--vertical`, or
  on a file whose frame size the probe cannot tell, is `Error:` and exit 1
  before cutting.
- **`plan_face_crop(track, width, height) -> CropPath | None`** in
  `core/framing.py`, pure: a window `min(round(h * 9 / 16), w)` wide,
  rounded down to even, following the largest face (by height). It holds
  while the face is within `DEAD_ZONE = 0.15` of the window's width of its
  centre, moves `EASE = 0.25` of the way per sample, jumps when the face
  moves more than `CUT_JUMP = 0.30` of the frame's width between samples
  (also across samples without a face), holds where there is no face, and
  stays inside the frame. Points are kept only where the window moves.
- **`ClipRenderer.render(..., crop=None)`** takes the `CropPath` (window
  size and left edges over time, from 0) in place of the fit filter:
  `sendcmd=f=crop.cmd,crop@face=W:H:X:0,scale=1080:1920,setsar=1`, then
  captions. `crop.cmd` is written next to `clip.ass` in the render's
  temporary folder, so no path reaches the filter's escaping rules (0010).
  The times count from the clip's start, which is what ffmpeg's filters see
  with `-ss` before `-i`.
- **Faces before the title**: a clip whose span has no face gets `Note: ...
  has no face in range; not rendered in the face style`, no file and
  `error: "no face found"` in the manifest, and no title request; it does not
  count toward exit 1. A face tracking failure is that clip's failure.
- **The manifest does not yet say which style** a clip was rendered in, so
  the review page's Save re-renders in `fit` (13d and 13e add variants and
  the style switch).
- Checked by hand: 5pk8HLcAAT0 rank 3 (44 s, faces tracked and rendered
  with captions in 11 s) framed each speaker and followed the camera cuts;
  v2917872953 rank 1 (43 s, 18 s) followed the people in the car, with one
  frame at the pump mostly an arm. Both 1080x1920 H.264.

## Amended by locked framing (after V5 item 13c)

The maintainer watched 13c's clips: the window followed the face in steps,
up to five a second, and looked choppy; it would look better locked on the
face, ignoring movement. On a podcast two-shot it also framed the person not
talking. `plan_face_crop` now holds one position per stretch and moves only
as a cut:

- **Shots** split where the subject jumps more than `CUT_JUMP = 0.30` of
  the frame's width between samples. The subject is the largest face, or two
  faces framed as a pair when the second is at least `PAIR_SIZE = 0.6` of
  the first's height and both fit within `PAIR_FIT = 1.15` windows.
- **In a shot** the window locks on the subject's median over the first
  `MIN_HOLD = 2.0` seconds; a stretch ends only where the subject then stays
  outside the window for 2 s, and each stretch holds at its own median.
  Brief movement (a gesture, leaning, another face for a moment) is ignored.
- A position held under 2 s keeps the previous one; a move under
  `MIN_MOVE = 0.05` of the window is not made; each move comes half a sample
  (0.1 s) before the sample that starts it, since a camera cut falls between
  samples. The dead zone and easing are gone.

Measured on 13c's two clips:

| Clip | Follow (13c): moves | Locked: moves | Notes |
|---|---|---|---|
| 5pk8HLcAAT0 rank 3, 44 s | 65 | 7 | 5 within a sample of the 8 camera cuts ffmpeg's scene detection finds; 2 re-locks while the speaker leans out for over 2 s |
| v2917872953 rank 1, 43 s | 52 | 3 | one handheld shot, no cuts |

- The two-shot that framed the listener is not fixed: there the speaker is
  side-on to the camera and barely detected. Mouth movement at 5 samples a
  second did not tell the two apart on that clip, and framing both did not
  apply. Speaker detection is a later item; until then `fit` is the pick for
  such clips (13d renders both).

## Amended by several styles in `drafts` (V5 item 13d)

- **`drafts --style`** is repeatable and defaults to `fit` and `face`;
  repeats are dropped and the order kept. `--style` with `--no-vertical` is
  `Error:` before any download. `render` keeps one `--style`.
- **File names**: `fit` keeps the clip's name (0008); another style's file is
  `<stem>.<style>.mp4` beside it (`variant_file_name` in `core/naming.py`),
  so `render --style face` now writes `.face.mp4` too.
- **One clip, several renders**: faces are tracked once, the title is written
  once and the captions built once; then each style renders in order. A clip
  with no face, or whose face tracking fails, still renders in `fit`: the
  first is a note, the second a failure (exit 1). A failed `fit` render ends
  the clip (an `error`, no files); a failed `face` render after it is a
  failure (exit 1) and the clip keeps its `fit` file.
- **Manifest** (0019): `variants` per clip and `styles` per run.
- **The review page** shows `file`, the `fit` one. Until 13e, a re-render or
  Trash there clears the clip's `variants` and deletes its other style files,
  since they no longer match the span; a style file that cannot be deleted is
  logged and does not stop the Trash.
- Checked by hand: `drafts` on 5pk8HLcAAT0 with the heuristic, top 2 and
  `--about`: both clips in both styles with one title each, in 67 s with the
  transcript cached.

## Alternatives considered

- **A zoom that sizes the crop to the face** (the face a fixed share of the
  output). It would handle small faces, but a facecam face is about 100 px
  tall, so blowing it up to 1080 wide is soft. The facecam split is the
  better answer for that layout.
- **MediaPipe** for face detection. A larger dependency with its own
  `numpy` and `protobuf` pins; YuNet through OpenCV was enough here.
- **Speaker detection** (diarisation, lip movement). Deferred; see 2.
- **Choosing one style per video automatically.** The layout changes within
  a VOD, and the maintainer's picks are the better judge until there is data.
- **Building in V6.** The podcast result was strong enough, and picking fits
  the review the maintainer already does.

## Consequences

- (+) Podcasts and webcam streams get a close, framed vertical clip.
- (+) The maintainer's style picks become data, like the judged labels.
- (-) About 25 s more per clip in `drafts` (detection plus a second render),
  some 4 minutes per VOD of 10 clips.
- (-) A 42 MB dependency and a model download on first use.
- (-) On IRL streams with several people, `face` cuts people out and may
  frame the wrong one; `fit` stays there to pick.
- (-) Facecam layouts get no good `face` crop until the split is built.

## Revisit when

- Picks show `face` rarely chosen on a kind of video: skip rendering it
  there.
- `face` frames the wrong person often: try speaker detection.
- Facecam clips are a large share of the posts: build the split.

## Related

- 0016: vertical clips (fit over a blurred fill)
- 0019: the folder per video and its manifest
- 0024: the review page
- 0003: dependency pins (`numpy` with the new dependency)
