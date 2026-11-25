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

## Amended by the style switch (V5 item 13e)

Picking happens in the review page (0024, style switch amendment): Save
keeps one style, deletes the other and records `style` on the judged
label, so the picks over time show which style wins on which videos.

## Amended by hand crops (V7 item 9h, ADR 0031)

- A clip may carry hand crops, made in the review page: a stretch in
  source seconds and a centre `x` (a fraction of the source width) for a
  full-height 9:16 window, where neither the fit nor the face track
  frames it well. At most 10, in order and apart, inside the span;
  `HandCrop` on the manifest clip and judged label.
- Face style: `with_hand_crops` (`core/framing.py`) puts the windows into
  the tracked crop path in their stretches and the tracked position back
  after each; the renderer moves it with `sendcmd` as before.
- Fit style: `hand_crop_plan` gives one window path moving at each
  stretch's start; the renderer splits the source, makes the fit and the
  window (`crop@hand` moved by `sendcmd` from `hand.cmd`), and lays the
  window over the fit with `overlay=enable` in the stretches. That
  doubles the scaling only, not the decoding.
- Times move to the cut clip through the time map; the run needs the
  frame size. Vertical folders only. Manual step RV23.

## Amended by the facecam spike (V7 item 6a)

A fixed facecam box per clip, found from the footage, for a later
`facecam` style (6b). Spike scripts in the session scratchpad, not
committed.

- Method, per 45 s stretch, frames at 2 a second scaled to 1280x720:
  1. YuNet faces (the face style's model and threshold); a face that
     stays put (centre within 4% of the frame width) in at least half
     the frames. Several such faces are tried, most frames first.
  2. From that face outward on each side, out to 6 face sizes, the
     nearest lasting straight edge: mean |Sobel| over the stretch at
     least 2.5 times the profile's median, and at least half its own
     strength along 80% of the side. The frame edge if none. First
     along the face's own extent, then along the box's sides clear of
     its corners.
  3. Kept if at least 15% of the frame each way, under half its area,
     not spanning its width or height, with at least three real
     borders (not the frame's edges). Else no box.
- Test set: 42 stretches. Facecam: two Sonoket Gaming VODs (a bordered
  cam, bottom right, moving between two positions during a stream) and
  stableronaldo's Fortnite day `v2922292514` (a borderless cam, top
  left, its layout changing: one cam, two stacked, full-screen webcam,
  desktop). No facecam: Floydsonson's Moon Sweepers (gameplay only), two
  CORE and one Lacy IRL VOD, the Iced Coffee Hour podcast. Boxes marked
  by hand on a pixel grid by the assistant, not the maintainer; a second
  set of 15 stretches checked by eye after the rules were set.
- Results: the bordered cam, 18 of 20 stretches boxed right (overlap
  with the marked box 0.83 to 0.99 on the 10 marked), 2 missed, none
  wrong. The borderless cam, of 10 stretches: 2 full-screen webcam
  stretches rightly given no box, 1 about right, 2 wrong (one where the
  layout changes inside the stretch), 5 missed. No box on any of the 12
  stretches without a facecam. About 3 s per 45 s stretch.
- Of the maintainer's 8 recorded posts, one is from a facecam VOD
  (`DQP3pmyJX4o`, the bordered cam); the other seven are IRL.
- So the box is found reliably for a bordered, fixed cam, and a miss
  falls back to `fit`. A borderless cam with changing layouts is mostly
  missed and sometimes wrong; a wrong box costs a pick of `fit` in the
  review page's style switch. 6b may build on these rules; whether the
  cam goes above or below the gameplay is decided there.

## Amended by the facecam style (V7 item 6b)

- A third vertical style, `facecam`, in `render` and `drafts` (opt-in:
  `--style facecam`; `drafts`' default stays fit and face). `--cam
  top|bottom` puts the cam above the gameplay (the default) or below it;
  `--cam` without the facecam style is an error.
- The box: a `FacecamFinder` Protocol in `core/`, `OpenCvFacecamFinder`
  in `adapters/media/facecam.py` with the facecam spike's rules and
  settings, its answer (box or none) cached per span under
  `<cache dir>/facecam/` as the faces are; `FacecamBox` (fractions of the
  frame) in `domain/`; `AppContext.facecam_finder`. YuNet's loading is
  shared with the face tracker (`load_face_model`).
- The frame: `plan_facecam` in `core/framing.py` gives a `FacecamLayout`:
  the box cropped and scaled to 1080 wide at its own shape, up to 960
  high; the gameplay a centre crop of the whole source at the shape of the
  rest; stacked by `vstack` in the one encode (ADR 0007), before captions.
  A cam near the frame's edge mostly falls outside the centre crop; a
  sliver of it can show at the gameplay's side. (12a amendment: the crop
  slides clear of it now.)
- No box: a note, and the clip renders in `fit` (or only in `fit` when
  `fit` is also chosen). A finder failure is a failure, as the face
  tracker's: the clip fails if facecam is its only style, else the other
  styles still render and the run exits 1.
- The manifest's facecam variant keeps `box` and `cam` (ADR 0019);
  `Label.style` and the review page's styles to keep take `facecam`. The
  page keeps a facecam draft as it is but does not re-render it (a Save
  that would is refused, nothing written) until 6c, which also brings the
  style switch's cam position. (6c amendment: it re-renders now.)
- The renderer refuses the facecam style with a face crop, hand crops,
  zooms or a sequence.
- Known: captions sit 420 px above the frame's bottom, so with `--cam
  bottom` they cover the cam (seen on a Sonoket clip); 6c or later can
  move them above it. (6c amendment: they sit above it now.) Measured on `DQP3pmyJX4o`: two 42 s clips with
  captions in 28 s, finder included. Manual step R23.

## Amended by facecam in the review page (V7 item 6c)

- The style switch shows Facecam beside the other drafts, and while it is
  picked, "Cam on top" and "Cam at the bottom". The live preview draws the
  facecam frame from the source player: the draft's box at full width over
  (or under) the centre crop, from a layout the server sends for either
  cam (`plan_facecam`, so the page does no layout maths). A new cam is an
  unsaved change and re-renders on Save.
- Save sends `cam` (top or bottom; anything else is 400) and records it on
  the judged label (`Label.cam`, ADR 0012) and the manifest's variant. A
  re-render in facecam finds the box again over the new span with the
  review run's finder, and keeps the draft's box if it finds none; with
  neither, the render fails as any re-render does. This replaces 6b's
  refusal.
- Captions over a cam at the bottom sit `CAPTION_GAP = 60` px above it
  (`facecam_caption_margin`), in `review` and in `render`/`drafts` alike,
  through `build_ass`'s `caption_margin_v` (ADR 0017); the preview draws
  them there too. This settles 6b's known clash.
- The facecam style has no zooms, hand crops, sequence or B-roll overlays:
  the Frame tab turns its add buttons off and says so, and a Save with any
  is refused (409) before anything is written.
- A thumbnail of a facecam clip is still framed as fit.
- Checked in headless Edge on a `DQP3pmyJX4o` drafts folder (fit and
  facecam): the toggle, both preview layouts, Save with the cam moved to
  the bottom (label `"cam": "bottom"`, captions above the cam in the
  re-rendered file), and a moved span re-rendered in facecam. Manual step
  RV32.

## Amended by the gameplay crop clearing the cam (V8 item 12a)

- Found on a Lacy VOD (`v2937465519`), a borderless cam in the top-left
  corner: the centred gameplay crop (353 to 1567 of 1920) took in the
  cam's right side (to 494) and the chat under it.
- `plan_facecam` still starts from the centred crop. If that shows any of
  the box, the crop slides away from the cam (away from the side of the
  frame the cam's centre is on) until clear of it, stopping at the
  frame's edge, with an even offset. It moves only if that shows less of
  the cam, so a cam in the middle that no slide can clear leaves the crop
  centred. A crop the frame's full width (a narrow source) slides up or
  down the same way.
- One place: `render`, `drafts`, `review`'s re-render and the preview's
  layouts all come from `plan_facecam`, so nothing else changes. Drafts
  rendered before keep their crop until re-rendered.
- The gameplay is no longer centred when the cam is near it, which is the
  point; what was at the far side of the frame may now show instead.
- Not settled here: the box itself. On that VOD it stopped at an edge
  inside the cam, and 9 of 10 drafts found none (a corner cam has two
  real borders, the finder wants three); 12b and 12c.

## Amended by the cam box set by hand (V8 item 12b-1)

- A borderless or corner cam is set by hand instead of found: a streamer
  rarely moves the cam within a stream, so one box serves a whole VOD,
  and often every VOD of a channel.
- Kept in two places. Per drafts folder, `RenderManifest.cam_box`
  (fractions of the frame, a `FacecamBox`; ADR 0019), for every clip of
  the folder. Per channel, a committed `data/cams.txt`
  (`CLIPPER_CAMS_FILE`), `<channel> | <x> <y> <w> <h>` per line, `#`
  comments, a channel once, compared as the channels file's are (ADR
  0018); `core/channels.py` parses it and rewrites one channel's line,
  leaving the rest.
- Which box: the folder's, else the channel's, else the finder's, else
  the draft's. `drafts` and `render` with the facecam style read the
  cams file with the other input checks (a bad file fails before any
  download) and, with a box for the video's channel, use it for every
  clip without running the finder, printing `Cam box (saved for
  <channel>)`.
- In `review`: `POST /api/cam-box` with `box` (or null to clear) and
  `channel` (true also writes or removes the channel's line; 409 when
  the video has no channel), the same JSON, Origin and Host checks as
  other writes, a box under 5% of the frame either way refused; the
  reply is the clips payload. With a hand box, Save may keep the facecam
  style on any clip of a vertical folder, and a facecam Save re-renders
  when the clip has no facecam draft or its draft is framed on another
  box. The payload's `cam_box` gives the box in use, whether the folder
  or the channel keeps it, the channel it may be kept for, and its frame
  for either cam; facecam drafts' preview frames follow the hand box, as
  Save would render them.
- A saved channel box goes stale when the streamer changes the overlay;
  it shows in the preview and is redrawn or cleared. `drafts` run again
  on a folder writes a new manifest, without the folder's box; the
  channel's stays. The page's drawing tool is 12b-2.

## Amended by the cam box in the review page (V8 item 12b-2)

- The Frame tab's "Cam box" says whether one is set (for the folder,
  saved for the channel, or none), with Draw (or Redraw) cam box, Clear,
  and "Keep for <channel>'s next videos" (ticked at first; shown only
  when the video has a channel).
- Draw switches to the Source view, pauses, and lays a layer over the
  picture alone (the player's letterbox left out): a drag draws the box,
  a drag inside it moves it, kept inside the picture; Esc or Cancel ends
  it unsaved. Save cam box sends it, rounded to four places as the cams
  file keeps it, below 5% either way not allowed; then the current clip
  is set to Facecam and shown in the Preview view, unsaved.
- With a box, every clip of a vertical folder drafted with styles offers
  Facecam; one without a facecam draft keeps playing the file it has in
  the Draft view, and the preview and cam buttons use the box's frame
  from the server. Clear sends no box (and takes the channel's line out
  with Keep ticked, or when only the channel had one), and drops an
  unsaved Facecam pick on clips without a facecam draft.
- `static/cambox.js`; `facecamLayouts` in `app.js` serves the preview
  and the cam buttons alike. Checked in headless Edge on a copy of the
  Lacy folder: drawn, moved, clamped, Esc, saved for the folder only,
  Facecam offered on a fit-only clip and saved in it (11 s, the whole cam
  over gameplay clear of it), no page errors. Manual step RV42.

## Amended by the speaker detection spike (V8 item 11)

The `face` style frames the largest face, which is sometimes the listener
(the locked framing amendment). Spiked on 35 stretches of 3 to 10 s with
two faces at least 10% of the frame tall, from cached face tracks of four
VODs: a podcast (5pk8HLcAAT0), two CORE IRL videos and a handheld Lacy
IRL stream. The maintainer marked who talks in each (left, right, both or
neither): 14 single-speaker stretches, 20 both, 1 neither. Bar set before
the run: the marked speaker on 80% of single-speaker stretches and better
than the largest face on 3 of 4 VODs.

| Method | Right of 14 | Podcast | 3Sbj_BZORr4 | ANoZNWQXYZ4 | Lacy |
|---|---|---|---|---|---|
| Light-ASD | 11 | 4 of 4 | 4 of 4 | 0 of 1 | 3 of 5 |
| Lip opening against loudness | 10 | 3 of 4 | 2 of 4 | 1 of 1 | 4 of 5 |
| Largest face (current) | 6 | 2 of 4 | 2 of 4 | 0 of 1 | 2 of 5 |
| Face nearer the centre | 6 | | | | |

- Lip opening: MediaPipe Face Landmarker's inner lips at 15 fps, the
  best correlation with loudness within 0.2 s, the higher of the two
  faces. Built by hand first, as the item ordered.
- Light-ASD (Liao et al., CVPR 2023; MIT; 1.02M parameters, weights in
  its repository, 94.1% mAP on AVA-ActiveSpeaker): its demo pipeline
  with YuNet in place of S3FD, every face in the frame found at 25 fps,
  IoU tracks, 112 px grey crops and MFCC, scores per frame. PyTorch on
  the CPU (about 120 MB), a few minutes for all 35 stretches.
- Light-ASD is sure on seated footage: 8 of 8 on the podcast and seated
  IRL, the speaker about +1.2 and the listener about -1.6 on the
  podcast. It is weak on the handheld crowd stream: scores near 0, a face
  broken into 13 to 27 tracks by the moving camera; two of its misses
  are stretches the maintainer called hard to judge. Its third miss
  (ANoZNWQXYZ4 16) scored an unmarked middle person highest, who the
  maintainer noted does most of the talking.
- Scoring every face mattered: several stretches' speaker was a third
  face outside the two compared, which Light-ASD scored and lip opening
  could not.
- On "both" stretches, both faces usually score as talking for part of
  the time: the per-frame scores follow turns.

Decision (the maintainer's, 2026-11-25): adopted for seated setups
(podcasts, seated IRL), the bar missed by one stretch on a small sample;
the maintainer uses `face` mostly for podcasts, not handheld streams.
Light-ASD becomes the speaker signal for `face`, in later items, each
designed first: first whether it runs through ONNX (`onnxruntime`, ADR
0003's check), so Clipper does not take PyTorch; then `face` framing the
speaker. Handheld crowd footage keeps the largest face. Scratch scripts
in `D:\Clippett\spikes\speaker\`, not committed.

## Revisit when

- Picks show `face` rarely chosen on a kind of video: skip rendering it
  there.
- `face` frames the wrong person often: try speaker detection (spiked
  in V8 item 11, adopted for seated setups).
- Facecam clips are a large share of the posts: build the split.

## Related

- 0016: vertical clips (fit over a blurred fill)
- 0019: the folder per video and its manifest
- 0024: the review page
- 0003: dependency pins (`numpy` with the new dependency)
