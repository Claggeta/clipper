# 0024: Review drafts in a local browser page

- Status: Accepted
- Date: 2026-11-09
- Amends: the "web UI" non-goal of V1 to V4 (vision), 0011 (one clip
  re-rendered on its own), 0019 (the manifest is edited between runs)

## Context

The maintainer reviews every folder of drafts by hand: watch each clip, note
whether to post, fix or drop it, and rewrite titles. In the trial week 6 of
11 keepers needed more at the end, and every judgment was written up by hand
afterwards. V5 measures picking changes against those judgments (0012 V5
amendment), which needs them recorded per draft with the span the maintainer
would have cut, and pooled across detector variants: more drafts to judge,
not fewer.

Fixing a clip today means cutting it again by hand outside Clipper. The
maintainer asked (2026-11-09) for a GUI: a list of clips, a player, a trim
slider showing the clip plus 2 minutes either side, a title box, a notes box,
and save and delete buttons, in a dark, clean style. It is for the period
until the drafts rarely need editing.

## Decision

1. **`clipper review <drafts folder>`** starts a local server on the
   loopback address and opens a page in the default browser. It reads the
   folder's `manifest.json` (0019), the analysis JSON it names and the source
   video. There is no URL input in V5: `drafts` makes the folder, `review`
   judges it.
2. **The page**: the clips listed down one side; a player; under it a film
   strip of the clip plus 120 s either side, clamped to the video, with two
   handles for start and end; a title box filled with the model's title; a
   notes box; Save, Trash and Add clip. Dark theme by default. Plain HTML,
   CSS and JavaScript shipped in the package, with no build step and no npm.
3. **The player plays the source video**, seeking inside the range, so
   dragging a handle needs no re-encode. Serving a multi-GB file to the
   browser needs HTTP range requests. Whether Python's standard library is
   enough or a framework is needed is decided in the first item, with a
   manual step, and any dependency is checked against 0003.
4. **The film strip** is thumbnails of the range made with ffmpeg through
   the shared helper (0004), cached per clip.
5. **Save** writes the clip's judged label (0012 V5 amendment): `post` if the
   span is unchanged, `fix` with the new span otherwise, and the notes. Then
   it re-renders that one clip with the folder's flags (vertical, captions)
   through the existing renderer, and updates its manifest entry, including
   the title as typed. What a failed re-render leaves behind is decided in
   its item; the label is written either way.
6. **Trash** deletes the clip file, marks its manifest entry dropped, and
   writes a rejected span to the judged labels.
7. **Add clip** marks a span the detector did not propose, from inside the
   current range, and saves it like any other clip: rendered, labelled
   `post`, listed in the manifest.
8. Loopback only, one user, no login. The server stops when the command
   stops.
9. **Tests**: the server's endpoints, and the label and manifest writes, are
   unit-tested with the fake renderer. The page itself is checked by manual
   steps; CI runs no browser.
10. Where the server code lives (under `cli/` or a front-end package beside
    it, as a second composition root using `AppContext`) is decided in the
    first item.

## Alternatives considered

- **A desktop window** (Qt, Tk). Tk has no video player. Qt's player on
  Windows depends on system codecs, and YouTube gave AV1 (0020), which
  Chrome plays; Qt was not tried. Both add a heavy dependency. Rejected.
- **Streamlit or Gradio.** Quick forms, but a custom two-handle film strip
  over a seeking player fights their models, and they are large
  dependencies. Rejected.
- **A keep/reject command in the terminal.** No video, so the maintainer
  still watches elsewhere and types times. Rejected.
- **The URL box now.** The page would own a 20-minute run with progress and
  failures, which is the second front end 0022 says should move the chain
  into `core/` first. Later, after the URL queue.
- **Record only on Save, re-render in a later batch.** Faster saves, but the
  folder would hold clips that do not match their labels until the batch
  runs. Rejected; the maintainer chose re-render on Save.

## Consequences

- (+) Judging drafts produces the labels V5 measures with, as a side effect.
- (+) Seconds dragged at each end measure the boundary work directly.
- (+) Fixed clips come out of Clipper, captioned and vertical, with no
  outside editor.
- (-) The first JavaScript in the repo, not covered by CI.
- (-) A later `render` or `drafts` run on the same folder replaces the
  manifest (0019 decision 3) and loses edits made in the page. The judged
  labels are separate files and survive.
- (-) Add clip only reaches moments within 2 minutes of a draft, so labels
  stay biased toward what some detector proposed.
- (-) The largest piece of V5, in several PRs.

## Revisit when

- The URL box is wanted: move the drafts chain into `core/` first (0022).
- Drafts rarely need editing: the page's job shrinks to judging.
- A second user or another machine needs it: loopback-only stops being
  enough.

## Related

- 0011: render's failure policy
- 0012: evaluation, and judged labels (V5 amendment)
- 0019: the folder per video and its manifest
- 0022: the drafts command
- 0023: candidate boundaries
