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

## Revisit when

- Repeats need edits that differ in more than zooms and crops.
- The preview's hitch at joins gets in the way of judging timing.
- Pieces from other VODs are wanted.

## Related

- 0007, 0012, 0016, 0017, 0019, 0024, 0025, 0029, 0030, 0031, 0032.
