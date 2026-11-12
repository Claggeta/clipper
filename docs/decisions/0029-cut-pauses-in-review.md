# 0029: Cut pauses out of a clip in review

- Status: Accepted
- Date: 2026-11-11
- Amends: 0017 (caption times), 0019 (manifest), 0024 (review page), 0025
  (face crop times)

## Context

Every draft is one continuous span of the VOD (0007, 0011). The maintainer
believes YouTube favours Shorts with more edits, and wants a "cut" in the
review page that removes the quiet stretches inside a clip. That belief is
unverified; the CSV import (0027) and the post record (0028) can test it
once cut clips have been posted.

The transcript already has word timestamps (0017), so the gaps in speech
inside a clip are known without another pass over the audio.

## Decision

1. **Pauses from word gaps.** A pure function in `core/` finds, inside a
   clip's span, every gap between consecutive words of at least
   `MIN_PAUSE_SECONDS = 0.6`, and proposes cutting it less
   `CUT_PADDING_SECONDS = 0.15` at each side, so no word is clipped. A
   segment without words (v0.3 transcripts) gives no cuts inside it.
2. **The review page.** "Cut silence" shows the proposed cuts as greyed
   stretches on the film strip; clicking one keeps it (a reaction or a
   punchline can be silent); "Clear cuts" removes them all. Preview skips
   the cut stretches. The clip list shows the length before and after.
   Manual cuts at any range are later, not part of this decision.
3. **Stored with the judgment.** Save sends `cuts`, a list of source-time
   ranges strictly inside the span, sorted and not overlapping. The judged
   label keeps its VOD span and gains `cuts` (default empty), so `eval`,
   overlap checks and the queue are unchanged. The manifest clip records
   `cuts` too, so a later re-render keeps them.
4. **One re-encode** (0007). With cuts, the renderer keeps only the parts
   between them and joins them (`trim`/`atrim` and `concat`) in the same
   filter graph as the vertical frame, the face crop and the captions.
   Without cuts the command is unchanged.
5. **One time map.** A pure function maps a source time to its time in the
   cut clip; caption events (0017) and face-crop moves (0025) go through
   it, and a word inside a cut is dropped. Changing the cuts re-renders, as
   a changed span does (0024 item 3d).
6. **Counted for later.** The queue page and the performance report can
   read a clip's number of cuts, so cut and uncut posts can be compared.

## Alternatives considered

- **ffmpeg `silencedetect` on the audio.** Finds truly quiet stretches
  only; streams with game sound or music are seldom quiet, and it costs a
  pass per clip. Word gaps find pauses in speech, and the toggle covers
  the silent moments worth keeping. Revisit if word gaps cut too much
  that is not quiet.
- **Cut every clip in `drafts`, unreviewed.** Possible later, once the
  maintainer's toggles show the defaults are trusted.
- **A second render pass to join parts.** Would break the one re-encode
  of 0007 and lose quality. Rejected.

## Consequences

- Jump cuts make the picture jump, more so in the face style; the 0.6 s
  minimum keeps breaths in.
- A cut clip's file length no longer equals its span; anything that
  assumed so (the ADR 0027 matching by length) must use the cut length.
- Captions and crop times depend on the cuts, so both builders take the
  time map.

## Amended by the cuts core PR (V6 item 4c, second PR)

- **`select`, not `trim` and `concat`**: the renderer drops the cuts with
  `select`/`aselect` on `between(t, ...)` and new timestamps
  (`setpts=N/FRAME_RATE/TB`, `asetpts=N/SR/TB`), first in the video filter
  and as an audio filter. `concat` needs the audio stream named, and the
  audio is optional (`-map 0:a:0?`, 0005). Still one encode.
- **The length limits the input**: with cuts, `-t` goes before `-i`. As an
  output limit, ffmpeg read past the span's end to make up the dropped
  frames, and a 30 s clip with 10 s cut came out 30 s long. `t` counts
  from the clip's start after `-ss`. Checked on `test_input.mp4`: 30 s
  with two 5 s cuts gave 20.02 s of video and 19.99 s of audio.
- `select` with `N/FRAME_RATE` assumes a constant frame rate; a variable
  rate source could drift out of sync. The manual step checks sync.
- `core/cuts.py`: `find_pauses`, `check_cuts`, `TimeMap` and `cut_crop`;
  `build_ass(..., cuts=)`; the renderer's `cuts`; `Label.cuts` (spans,
  written to the millisecond) and `ManifestClip.cuts`. Nothing sets cuts
  until the review page's PR.

## Amended by the review page PR (V6 item 4c, third PR)

- `GET /api/pauses?start=&end=` returns `find_pauses` for the span the
  handles show, so the proposals follow the handles; 409 when the folder
  has no transcript. On a real 43 s Stable Ronaldo clip it proposed 8
  cuts, 9.1 s in all.
- The page keeps each clip's cuts, cut or kept, until Save; Save sends
  only those cut and inside the span. Reset and "Clear cuts" empty them.
- A Save re-renders when the cuts differ from the file's (to the
  millisecond), as for a moved span; the manifest records the file's
  cuts and the label the saved ones. Cuts never change the verdict.
- Manual step RV12. The film strip's handles had stopped drawing since
  the rating PR (#190, a second `drawMarks` in the page script), fixed
  first in #195.

## Amended by manual cuts (V7 item 3a)

- Cuts are also marked by hand: `Cut in` and `Cut out` (keys `[` and `]`,
  ignored while typing; Esc drops a pending Cut in) add the range between
  two playhead times as a cut. It must lie strictly inside the span, at
  least 0.1 s long; one that overlaps or touches other cuts is merged with
  them, since the renderer needs cuts apart. It toggles, previews and saves
  like a pause cut, and is striped in the accent colour.
- On disk a manual cut is an ordinary cut: no `kind` field on the label
  or manifest, so the renderer, captions, face crop and `eval` are
  unchanged. The page therefore treats saved cuts as manual after a
  reload. Recording the kind would let manual and pause cuts be counted
  apart, but no measure needs it yet.
- `Cut silence` no longer replaces every cut: it replaces the pause cuts
  it proposed before, keeps manual and saved ones, and leaves out a pause
  that meets one of them. `Clear cuts` and Reset still empty the list.
- Manual step RV16. Dragging a range on the strip is item 3b.

## Revisit when

- The maintainer keeps most proposed cuts, or turns most off: change the
  defaults or cut in `drafts`.
- The performance report (V6 item 7) shows cuts do or do not help.

## Related

- 0007 (one re-encode), 0017 (word captions), 0019 (manifest), 0024
  (review page), 0025 (face style), 0027 and 0028 (posts and performance).
