# 0032: Summarize a VOD's clips into one longer Short

- Status: Accepted
- Date: 2026-11-13
- Amends: 0019 (manifest), 0024 (review page), 0028 (post queue, in the
  second PR)

## Context

The maintainer plans clips from streams of iterative games, which start
as boring tasks with very large goals and ramp up over hours. On
2026-11-13 they asked for a button in the review page that condenses a
VOD's clips into one longer Short summarizing the episode, while each
clip can still be posted on its own. YouTube Shorts run up to about
three minutes.

The review page already saves each clip with its edits (span, cuts,
captions, text, framing, zooms, audio) and re-renders it (0024, 0029,
0030, 0031); the renderer mixes music (0007, audio amendment).

## Decision

1. **Joined from the saved clips.** A Summarize button opens a dialog
   listing the folder's rendered, untrashed clips in VOD order, the
   saved ones ticked, each with its length and an optional label card
   ("Hour 3", filled by an "Hour N cards" button from its time in the
   VOD), and an optional music file. A clip with unsaved edits is
   flagged, since the summary uses its file.
2. **One more encode.** `ClipRenderer.join` takes the clips' files and
   joins them (`concat` over each file's video and first audio, after
   `setsar=1`), burns the cards (on-screen texts from 0030, middle, the
   first 2.5 s of each clip, one ASS on the joined clip's times) and
   mixes the music as a clip's (0007, audio amendment, the joined sound
   as the voice). It is a second encode of encoded clips: small, real
   loss at the same quality, against rebuilding every clip's framing,
   captions and zooms from the source in one graph.
3. **One per folder.** `<stem>_summary.mp4`, replaced when made again;
   its title and rating stay, its thumbnail goes.
4. **Its own manifest entry.** `RenderManifest.summary`: the file, the
   parts (rank and card) in VOD order, the music, its length, and the
   title, rating and thumbnail set in the page. A summary is not a
   moment, so it writes no judged label and `eval` ignores it.
5. **Handled in the page.** A Summary entry above the clip list opens
   it: play, a title with "Suggest" (the local model, 0018, given the
   clips' words in order; refused without `CLIPPER_LLM_MODEL`), Good or
   OK, a thumbnail from its own frame, Remake, Trash.
6. **No new picking.** It joins the moments kept; filling gaps in a long
   climb with suggested moments comes only if the plain version shows a
   need.
7. **Over the limit is a warning**, not refused: the dialog shows the
   total in the warning colour past 180 s.
8. **In two PRs**: the summary itself (this), then `queue` and `posted`
   (0028).

## Alternatives considered

- **Re-cutting every part from the source in one encode**: no second
  encode, but each clip's style, face or hand crops, zooms, captions,
  texts and audio rebuilt in one graph per part. Rejected for now.
- **`concat` demuxer without re-encoding**: needs identical streams and
  cannot burn cards or mix music.
- **A judged label for the summary**: it spans many moments and would
  confuse `eval`'s hit rule.

## Consequences

- A summary reflects its clips as last saved; editing a clip later does
  not change it until Remake.
- Joining takes about a quarter of real time: 80 s of clips in 20.5 s
  on v2924392099.

## Revisit when

- The plain summary leaves gaps the maintainer wants filled.
- The second encode shows visibly.

## Related

- 0007, 0018, 0019, 0024, 0028, 0030, 0031.
