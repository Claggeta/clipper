# 0036: Hand clips to DaVinci Resolve as a timeline

- Status: Accepted
- Date: 2026-11-20
- Amends: none (a new command beside `review`; ADR 0031's editor stays)

## Context

Drafts are not good enough to post as cut (V8 rescope, 2026-11-17), and
the maintainer has found cutting, inserting and splicing in the review
page too hard to use, so no saved clip had a splice or B-roll. DaVinci
Resolve's free edition is a full editor; the cost of using it is starting
from an empty project and finding a moment in a 5-hour VOD.

**Spike** (V8 item 8, 2026-11-20; files in `D:\Clippett\spikes\resolve`,
not committed). Two saved clips from v2927846652 with their pause cuts and
a 2 s B-roll GIF between them, handed to Resolve three ways, each imported
by the maintainer (new to editors) following a step sheet:

| Test | What Resolve got | Result |
|---|---|---|
| A, finished pieces | each clip rendered vertical without captions, its captions as a transparent ProRes 4444 layer above, in an FCPXML 1.9 timeline | imported and played right |
| B, the source | an FCPXML timeline of the source VOD's kept stretches, one timeline clip each, the B-roll and caption layers above | imported and played right; a cut opened back up by dragging an edge, the baked caption layer cut off where the timing changed |
| C, subtitles | the captions as an SRT file on Resolve's subtitle track | imported, in time, words editable |

- ffmpeg's `ass` filter leaves alpha untouched; the caption layer was made
  by burning the captions on black and on white and taking the difference
  as alpha.
- B needed no render, so it is instant, and it keeps every cut open to
  re-trim. A baked caption layer cannot follow a re-timed edit;
  subtitles can, at the cost of the styling (one word highlighted).
- B placed the 1920x1080 VOD as Resolve fits it, with bars above and
  below; the maintainer chose to fill the height instead.

## Decision

1. **`clipper resolve <clip> [<clip> ...]`**: clips named as `clipper
   posted` takes them (title, path or `<folder>/<file>`), from any drafts
   folders, joined in the order given. Joined clips (Combine, Summarize)
   are refused; their clips are handed off instead.
2. **It writes `<clips dir>/resolve/<name>/`**, the name the first clip's
   title as an upload name (ADR 0028) unless `--name` gives it:
   - `timeline.fcpxml`: an FCPXML 1.9 project at 1080x1920 and 60 fps
     (one rate whatever the sources'; Resolve conforms them), with each
     clip's kept stretches of its source video (its span less its cuts,
     or its sequence's blocks, ADR 0034) and its B-roll and library
     inserts (ADR 0035) in turn on one track. Media is referenced in
     place, nothing copied or rendered.
   - Source stretches are scaled to fill the frame's height
     (`adjust-transform`, 3.16 for a 16:9 source), cropping the sides,
     centred. B-roll stays fitted, as review inserts it; a B-roll insert
     without sound is turned down (`adjust-volume`).
   - `captions.srt`: the captions as subtitles in timeline time, from
     the same chunks as the burned captions (`caption_chunks`, with the
     clip's caption fixes and hidden words), split at cuts; left out by
     `--no-captions`.
   - `steps.txt`: the import steps, also printed.
3. **Not carried over**: on-screen texts, zooms and hand crops, the face
   and facecam styles, B-roll overlays, music, sound effects and the
   captions' styling. Edits made in Resolve do not come back; picking
   keeps learning from verdicts in `review`.
4. The timeline is built by pure code in `core/handoff.py`; the command
   only finds clips, probes media and writes files.

## Alternatives considered

- **Finished pieces (test A).** Worked, but needs a render per clip and
  gives no way back into the VOD past the draft. Kept as a possible
  `--styled` option if baked, styled captions are missed.
- **OTIO.** Resolve imports it too, but FCPXML worked first time and needs
  no new dependency.
- **The blurred fill or the face crop in Resolve.** Neither maps to an
  FCPXML transform without effects Resolve may not import; filling the
  height was the maintainer's choice.
- **Burning the captions as a transparent layer by default.** It cuts off
  as soon as the timing changes, which is the reason to use Resolve.

## Consequences

- Splicing clips from several VODs, and B-roll, is done in Resolve; a
  hand-off takes seconds.
- A 16:9 source loses its sides; a face off-centre may need moving in
  Resolve (Inspector, Transform, Position).
- A moved or deleted source video breaks the timeline's links, as in any
  Resolve project.
- Resolve's reading of the fill scale and of the muted inserts is checked
  by hand (manual step H1), not in CI.

## Revisit when

- The maintainer misses the styled captions: add the transparent layer
  from test A as an option.
- Zooms, texts or audio edits from review are wanted in Resolve.
- Resolve stops importing FCPXML 1.9, or a source's rate makes the 60 fps
  timeline wrong.

## Related

- 0024 and 0031 (the review page), 0028 (upload names), 0029 (cuts), 0034
  (sequences and B-roll), 0035 (the clip library).
