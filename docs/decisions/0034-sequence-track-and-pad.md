# 0034: A sequence track and a pad in the review editor

- Status: Accepted
- Date: 2026-11-15
- Amends: 0007 (one encode, with B-roll inputs), 0017 (captions across
  blocks), 0019 (manifest), 0024 and 0031 (review page), 0025 (face crop
  per block), 0029 (cuts as block splits; `select` replaced), and the V7
  item 9m plan (B-roll lands on the pad)

## Context

On 2026-11-14, after V7 item 5, the maintainer asked for easier editing
in `review`: a second film area where cut sections and B-roll are
dragged and dropped to build the finished clip, and a scratch pad where
a section is cut and kept for later use while editing.

A clip today is one span of the VOD less its cuts, always in VOD order
(0029). Caption fixes, zooms, hand crops and face-crop moves are keyed
to VOD time and moved into clip time through one time map; on-screen
texts, sound effects and music are already in the finished clip's
seconds (0030, 0007 audio amendment). The renderer keeps the parts with
`select`/`aselect` in one encode, which can drop ranges but cannot
reorder them or bring in footage from elsewhere. B-roll was planned as
V7 item 9m: the maintainer's own files from `data/broll/`, as a
full-frame cutaway or a picture-in-picture box over the clip.

The maintainer's choices (2026-11-15): a stretch of the VOD may be used
more than once in a clip (an instant replay); the pad holds pieces of
the VOD being reviewed; B-roll is an overlay or an inserted block; this
goes next, then 9m on top of it.

## Decision

1. **A clip is a sequence of blocks.** A block is a VOD range
   (`source`: start and end) or a B-roll file (`broll`: file, in and
   out within it, audio on or off), in play order. Stored as `sequence`
   on the manifest clip and the judged label. A clip whose blocks are
   its span's parts in VOD order with nothing inserted is saved as
   span and `cuts` exactly as now, so older files and clips edited only
   with cuts are unchanged; anything else is saved with `sequence` and
   no `cuts`.
2. **The span stays the moment.** The judged label's start and end are
   the span between the start and end handles, as now; blocks from
   inside it are the clip's own parts, blocks from elsewhere are
   inserts. `eval`, the hit rule (0012) and Save's overlap check (0024)
   use the span only.
3. **Repeats.** A VOD range may appear in more than one block. Clip
   time maps to one VOD time (or a B-roll file time); VOD time maps to
   a list of clip times. Edits keyed to VOD time (caption fixes, hidden
   and key words, zooms, hand crops, face-crop moves) apply to every
   occurrence. A zoom or hand crop may instead be marked "this block
   only": it is stored on the block, in seconds from the block's start,
   and applies there alone. Texts, sound effects and music stay in clip
   seconds.
4. **The pad.** A list per drafts folder, `RenderManifest.pad`: pieces
   with an id, a name (editable, first the transcript's first words)
   and a VOD range. A range marked with Cut in and out (`[`, `]`) or a
   drag on the strip goes to the pad with one key or button; a piece is
   dragged from the pad onto the sequence as a new block. A block copies
   its piece's range, so deleting or renaming a piece changes no clip.
   Pieces of other VODs are out of scope.
5. **B-roll.** Files from `data/broll/` (`CLIPPER_BROLL_DIR`): video
   (`.mp4`, `.mov`, `.webm`) and images (`.png`, `.jpg`), listed in the
   pad beside the pieces. Dropped on the overlay lane, a file covers a
   range of the clip in clip seconds, as a full-frame cutaway or a
   picture-in-picture box placed and sized by dragging on the preview;
   the clip's sound continues. Dropped on the sequence, it is an
   inserted block, fitted to the frame on a blurred fill like the
   source, with its own audio or silence; captions pause over it, and
   face or hand crops and zooms do not apply. An image is a still of a
   set length. Built in 9m, after this ADR's other PRs.
6. **One encode.** Each block is trimmed (`trim`/`atrim`) from the
   source or read from its B-roll input, framed, and the blocks are
   joined in order (`concat`), with generated silence for a block
   without sound; captions, texts, overlays and the audio mix are laid
   over the joined clip in clip seconds. This replaces `select`/
   `aselect` (0029 core amendment). Whether framing is applied per
   block before the join or after it through clip-time expressions is
   decided in the core PR, by which keeps the graph testable.
7. **Captions** come from each source block's words in play order,
   mapped to clip time; a chunk never spans a join, and a word cut by a
   block's edge follows the existing rule (dropped if it starts before
   the block). The face crop is tracked over each block's VOD range, as
   now over the span (0025).
8. **The page.** The timeline gains a Sequence lane in clip time, with
   the Text, Zoom, Music and SFX lanes under it in the same time; the
   frame strip and waveform above stay in VOD time, for marking ranges.
   Blocks are dragged to reorder, their edges dragged to trim, deleted
   with Delete, and duplicated with D (a repeat). The Clip tab shows the
   selected block. The pad is a panel below the clip list. The live
   preview plays the blocks in order by seeking the source player, and
   B-roll in a second player; a join may hitch in the browser, and
   Save's render is exact. Mock: `docs/mockups/review-sequence.html`.
9. **In PRs**, each with a manual step: this ADR and the mock (9n-1);
   the core (blocks, the time map with repeats, block-only edits, the
   renderer's join, manifest and label fields; 9n-2); the Sequence lane
   and the preview through blocks (9n-3); the pad (9n-4); then B-roll,
   as overlays and inserts (9m).

## Alternatives considered

- **Every edit re-keyed to clip time**, so a repeat is edited apart
  from its original: the most flexible, but every lane, stored edit and
  judged label moves to a new time base, with a migration. Rejected for
  now; block-only zooms and crops cover the instant replay.
- **A pad across VODs**: several sources in one render, and pieces whose
  video must be found by stem. Later, if a need shows.
- **B-roll as overlay only**: smaller, but the maintainer wants inserts.
- **Building the clip from Combine's join of rendered files** (0032):
  a second encode per block and no shared framing. Rejected.

## Consequences

- The largest model change since cuts: the time map, the renderer, the
  preview and every edit lane pass through blocks.
- Renders with many blocks take longer, and the graph grows with each.
- A zoom on a repeated moment shows at every occurrence unless marked
  block-only.
- Labels with a `sequence` are larger; `eval` reads only their span.
- Combine (0032) and Summarize (0033) are unchanged: they join rendered
  clips and beats.

## Amended by the core (V7 item 9n-2)

- **Sound effects are in source seconds**, not clip seconds as decision
  3 said: `SoundEffect.at_seconds` is a moment of the source (ADR 0007,
  audio amendment). In a sequence an effect plays at every block that
  shows its moment, and a block may have its own effects, in seconds
  from its start, which play there as well (needed for an effect on a
  repeat, and in 9m on a B-roll insert). Only on-screen texts and the
  music are in clip seconds.
- **Model.** `SequenceBlock` (a source range, its own `zooms`,
  `hand_crops` and `effects`), `sequence` on `ManifestClip` and `Label`
  (at most 30 blocks, never with `cuts`), and `PadPiece` in
  `RenderManifest.pad` (unique ids, at most 50). Blocks are written in
  seconds, like zooms. B-roll blocks come with 9m.
- **Time map.** `BlockMap` in `core/sequence.py`. A zoom or hand crop in
  source seconds is placed in every block it falls in; in a block with
  its own zooms (or hand crops), the clip's do not apply there. A range
  across the join of two blocks in source order is one piece, so a
  smooth zoom across a cut eases once.
- **Cut clips are unchanged.** A clip with no sequence keeps the span,
  cuts, `TimeMap` and `select` path as before, so its files and labels
  are byte for byte what they were; only a clip with a sequence takes
  the block path. That is two paths in the store and renderer, kept for
  safety rather than folding cuts into blocks.
- **Captions.** `build_ass(..., sequence=)` captions each block as a
  clip of its own and moves it to its place; edits apply at every
  occurrence.
- **Face crop.** Each block is tracked and framed on its own (cached per
  range); a block with no face holds the window in the middle, and no
  face in any block is an error, as before.
- **Renderer.** Decision 6's choice: the frame, crops, zooms and
  captions run after the join, on clip-time expressions, as they ran
  after `select`. Each block is an input of its own (`-ss`, `-t`), joined
  by `concat` in `-filter_complex`; the mix takes the joined sound and
  the music and effects as inputs after the blocks.
- **Save** takes `sequence` (blocks of `start`, `end` and their own
  edits); with one, the clip's zooms, hand crops and effects may lie
  anywhere between the earliest and latest second its span or blocks
  cover. Re-rendered when the sequence changes.
- Manual step R21, run 2026-11-15 on 3Sbj_BZORr4: three blocks (a
  repeat with its own 2.0x zoom), face style, captions, music and two
  effects; 14.03 s, 6.9 s to render, frames as expected.

## Amended by the Sequence lane (V7 item 9n-3)

- **The lane** is under the strip, in clip time, with its own playhead.
  A clip shows the blocks its span and cuts make, faint; the first edit
  makes the sequence its own, and `Back to span` drops it.
- **Keys** differ from decision 8, whose `D` and `P` were taken (`D`
  switches the view, `P` is Preview span): `B` splits at the playhead,
  `Shift+D` duplicates the selected block, `Backspace` removes it
  (`Delete` is Trash), `Alt+←`/`Alt+→` move it; blocks are also
  dragged, and their edges dragged to trim (snapped to words).
- **Cuts are off** while a clip has its own sequence: Cut in and out,
  Cut silence and a drag on the strip say to split and remove blocks
  instead, and no cuts are sent. The handles still set the span.
- **The other lanes stay in source time** for now, not clip time as
  decision 8 said: captions, zooms, hand crops and the face crop look
  themselves up by source time, which a repeat shares, so the preview
  needed no change for them. Texts, music and the time readout use the
  block playing.
- **Save** sends the span and cuts when the blocks are in source order,
  apart and run from the start handle to the end handle with no edits of
  their own (decision 1); otherwise `sequence` and no cuts.
- "This block only" zooms, hand crops and effects in the Frame and Audio
  tabs are left to a later item (9n-5); the core already takes them.
- Manual step RV27.

## Amended by the pad (V7 item 9n-4)

- **To the pad**: `Shift+P` or `To pad` sends the range from the Cut in
  mark to the playhead, or the span when no mark is set; a `Shift`-drag
  on the strip sends the dragged range (a plain drag still cuts). `[`
  now sets its mark anywhere, outside the span or in a clip with its own
  sequence, for the pad; `]` still needs a cut inside the span.
- **Server**: the clip list returns `pad`; `POST /api/pad` adds a piece
  (`start`, `end`, optional `name`), `/api/pad/<id>` renames it,
  `/api/pad/<id>/delete` deletes it, each with Save's checks and
  replying with the whole pad. Ids are `p<n>`, one past the highest.
  At least 0.5 s, at most 50 pieces, names one line of 60 characters.
- **Default name**: the words spoken in the range, cut at a word to 60
  characters, or `piece at H:MM:SS` when nothing is said or the folder
  has no captions.
- **The panel** is below the clip list, the clips scrolling above it:
  each piece's first frame, name (renamed in place), times, `Play`,
  `Add` (after the selected block) and `×` (twice). A piece is dragged
  onto the Sequence lane as a copy of its range.
- Manual step RV28.

## Amended by B-roll in the core (V7 item 9m-1)

9m is in two PRs: the core (this) and the page (9m-2).

- **Files**: `data/broll/` (`CLIPPER_BROLL_DIR`), `.mp4`, `.mov` and
  `.webm` videos and `.png`, `.jpg` and `.jpeg` stills. `GET
  /api/broll` lists them with each video's length and whether it has
  sound (`MediaInfo.has_audio`, probed once per run; an unreadable file
  is listed with no length); `/broll/<name>` serves listed names only.
- **Inserts**: `SequenceBlock.broll` names the file; its `start` and
  `end` are times in the file (a still starts at 0, at most 30 s), and
  `sound` plays the file's audio, or silence when off, for a still, or
  for a file without audio. It takes no zooms or hand crops; its own
  effects play. Source-time edits and captions skip it.
- **Overlays**: `broll` on the manifest clip and label, `BrollOverlay`
  of `file`, `start_seconds` and `end_seconds` (the finished clip's),
  `offset_seconds` into the file, `mode` (`cutaway` fitted on a blurred
  fill over the whole frame, or `pip`, a box of `size` of the frame's
  width centred at `x`, `y`). Silent; the clip's sound continues. A
  clip with overlays and no sequence renders as its span less its cuts,
  as blocks.
- **Framing per block** replaces 9n-2's framing after the join, as the
  design said: a face crop after the join would cut a landscape insert
  to its middle third. Each source block is fitted, face-cropped (its
  own tracked crop, its own `cropb<n>.cmd`) or hand-cropped on its own,
  each B-roll block fitted on a blurred fill (to the source frame in a
  landscape clip, which then needs the frame size), all `yuv420p`; then
  `concat`, the zooms, the overlays (`overlay=enable`, the input shifted
  to its start), the captions. Clips without a sequence or overlays
  keep the single-input path.
- **Save** refuses, before writing, a missing file, a block or overlay
  needing more of a video than it has, and an overlay past the clip's
  end. Labels write `broll` and `sound` only on B-roll blocks.
- Manual step R22.

## Amended by B-roll inserts in the page (V7 item 9m-2a)

- **The pad** lists `/api/broll` under its pieces: first frame, length
  or "still", 🔊 when the file has sound. `Insert` puts a file after the
  selected block, a drag drops it on the Sequence lane: the first 3 s of
  a video (all of it if shorter) or 2 s of a still, sound on when the
  file has any.
- **On the lane** an insert is green with its file name. Its edges trim
  within the file (to its length; a still keeps 0 as its start, at most
  30 s), unsnapped. `Sound on`/`Sound off` in the lane's bar toggles the
  selected insert, greyed out for a still or a file without sound.
  `B` splits an insert at the time shown; a still is not split. A
  sequence with an insert is always sent as a sequence.
- **Preview**: clicking an insert cues it in a hidden second player (an
  image for a still); playing the blocks hands over to that player at an
  insert, the source waiting, and back at the next block. The preview
  draws the insert fitted on its blurred copy, with no captions, zooms
  or crops; texts and music go on in clip time. A still's end is timed
  as well as watched per frame, since a page drawing no frames would
  otherwise wait.
- **Fix**: setting the source player to the time it already has fires
  `seeking`, which ended a preview that had just begun; Preview span
  and playing the blocks now set the time only when it changes.
- Manual step RV29a.

## Amended by B-roll overlays in the page (V7 item 9m-2b)

- **The lane**: "B-roll over", under the Sequence lane, in the finished
  clip's seconds over its whole length (the span less its cuts, or the
  blocks). `Overlay` on a pad file, or a drop on the lane, adds a 2 s
  cutaway at the playhead or the drop (shorter if the clip or file ends
  first), at most 10. A bar drags to move, its edges to trim, at least
  0.5 s; trimming the start moves `offset` with it, never below 0, and
  the end stops at the clip's end and the video's. A click selects it
  and shows the clip there. A bar past the clip's end, after a sequence
  edit, is outlined red; Save's check refuses it as before.
- **The row** for the selected overlay: `Cutaway` or
  `Picture-in-picture`, a size of 15 to 100% (picture-in-picture only)
  and `Remove`.
- **Preview**: overlays are drawn at clip time after the framing and
  zooms and under the captions and texts, over inserts too, as the
  renderer lays them; a cutaway fitted on its blurred copy, a box of
  `size` of the width centred at `x`, `y`. A third hidden player, muted,
  plays the first video overlay shown, put back in step past 0.25 s of
  drift; a second overlapping video overlay is not previewed (the render
  shows both). A box dragged on the preview moves the overlay, after
  texts and before zooms and hand crops.
- **Save** sends `broll`; the unsaved mark, the re-render check and
  Reset include it.
- Manual step RV29b.

## Revisit when

- Repeats need edits that differ in more than zooms and crops.
- The preview's hitch at joins gets in the way of judging timing.
- Pieces from other VODs are wanted.

## Related

- 0007, 0012, 0016, 0017, 0019, 0024, 0025, 0029, 0030, 0031, 0032.
