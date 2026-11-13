# 0030: On-screen text and thumbnail images, made in review

- Status: Accepted
- Date: 2026-11-11
- Amends: 0017 (captions file), 0019 (manifest), 0024 (review page)

## Context

The maintainer wants more edited Shorts: a hook on screen over the first
seconds, and other lines of text where they fit, and a thumbnail made
while reviewing. One campaign brief asked for an on-screen hook (Later,
"Campaign briefs"). YouTube Studio offers an image upload for a Short's
thumbnail as well as a frame it picks; the maintainer has used the picked
frame so far.

Captions are already one ASS file per clip, burned in by the one encode
(0007, 0017), and the review page re-renders a clip on Save (0024), with
cuts since 0029.

## Decision

1. **Text boxes per clip.** A clip may carry texts, each with its words
   (one line typed, wrapped to fit), a start and end in seconds of the
   finished clip (after cuts, so "the first 3 seconds" means what viewers
   see), and a place: `top` (below the area YouTube's own interface
   covers) or `middle`. Not the bottom, where captions and YouTube's
   buttons are.
2. **One look**: large bold white text on a half-dark box (ASS opaque box),
   a style of its own beside the word captions, scaled for the vertical
   and the landscape frame.
3. **Same file, same encode.** The texts are extra events in the clip's
   ASS, so they cost no extra pass; they are burned whether or not the
   folder has word captions, in every style.
4. **Made in review.** "Add text" adds a box; the first starts as the
   clip's title (the hook sentence is too long for the screen), shown 0 to
   3 s at the top. The page shows the texts over its player at about their
   place; the re-rendered draft is the exact look. Save stores them on the
   manifest clip and the judged label, and re-renders when they changed,
   as for a span or cuts.
5. **Thumbnails as images.** "Thumbnail" saves the frame at the playhead as
   a 1080x1920 JPEG in the clip's own framing (fit or face), without word
   captions, with an optional line of text in the same look, as
   `<clip stem>.thumb.jpg` beside the clip, for upload in Studio. The
   queue page lists it with the clip (0028).
6. **Counted for later.** Whether a post had a hook or a thumbnail is on
   the manifest, for the performance report (0027).

## Alternatives considered

- **A second encode for text** (`drawtext` after the clip): loses quality
  and repeats 0007's single path. Rejected.
- **The hook sentence as on-screen text**: up to 15 words, too long to read
  in 3 s. The title (8 words or fewer) is the starting text.
- **Free placement by dragging**: more page code for little gain; two
  places cover hooks and reactions. Revisit if they do not.
- **A thumbnail frame placed at the clip's start** for YouTube's frame
  picker: not needed, since Studio takes an image.

## Consequences

- The page's preview of text is approximate (browser fonts, not libass).
- Texts with no clip time left after cuts are dropped from the render.
- Thumbnails are files the queue lists; posting stays manual.

## Amended by the text rendering PR (V6 item 4e, second PR)

- `OnScreenText` (text up to 120 characters, one line; start and end in
  the finished clip's seconds; `top` or `middle`) on `ManifestClip.texts`
  and `Label.texts`; `build_ass(..., texts=)` adds a `Text` style only
  when a text is shown, and `Captions.speech` says whether any caption
  came from the transcript, so a clip with texts and no speech is not
  recorded as captioned.
- Each text is pinned with `\pos` (top: 540,300 on the vertical canvas;
  middle: 540,960; landscape 960,80 and 960,540). Without it, libass moved
  a middle line up under a top line shown at the same time.
- Emoji render, through a fallback font, in monochrome outline: libass
  draws no colour emoji. Titles' emoji stay; the maintainer can remove
  them from a text.
- Checked on a real render of `test_input.mp4` with a cut, word captions,
  a top hook and a middle line.

## Amended by the review page PR (V6 item 4e, third PR)

- The page's "On-screen text" rows: words (up to 120 characters), from
  and to in seconds of the finished clip, Top or Middle, Remove; at most
  5 per clip (`MAX_TEXTS`). "Add text" starts the first as the title, 0
  to 3 s at the top, and later ones empty in the middle.
- Unsaved texts are drawn over the draft player at about their place;
  once saved they are burned into the draft, so nothing is drawn.
- Save sends `texts`; the server checks them like the model and
  re-renders when they differ from the file's. In a folder without
  captions the texts are burned alone, and the manifest's `captions`
  stays empty. Reset removes them. Manual step RV13.

## Amended by the thumbnails PR (V6 item 4e, last PR)

- `ClipRenderer.still`: one frame at a source moment, `-ss` before `-i`,
  `-frames:v 1`, JPEG quality 2, framed by the fit filter or the face
  crop held where it starts, with the text's ASS from time 0. Checked on
  `test_input.mp4`: a 1080x1920 JPEG of about 90 KB.
- The page's Thumbnail row: optional text with Middle (the default for a
  thumbnail) or Top, and "Make thumbnail" for the frame at the source
  player's playhead, in the style shown (the request carries `style`, as
  Save does). `POST /api/clips/<rank>/thumbnail` writes
  `<clip stem>.thumb.jpg` (the same name for both styles) through a
  `.part.jpg`, records `ManifestClip.thumbnail`, and the page shows it
  from `/thumbs/<name>`, only names the manifest lists. A face thumbnail
  with no face at that moment is framed as fit, with a note; a clip
  without a file is refused until saved. Making it again replaces it;
  Trash deletes it. It does not touch unsaved edits.
- The queue page lists each clip's thumbnail path (0028). Manual step
  RV14.

## Amended by the one box fix (V7 item 7e)

- The maintainer found that a text wrapping onto a second line showed a
  darker band between the lines, on clips and thumbnails. The cause:
  ASS `BorderStyle 3` draws a box per line, padded on every side by the
  outline (18 px vertical, 14 px source), and lines sit one text height
  apart, so neighbouring boxes overlapped and the half-transparent
  overlap printed darker.
- The `Text` style now uses `BorderStyle 4`, libass's one box behind the
  whole event, in the back colour (`&H80000000`) and padded by the
  outline as before, with the outline colour clear (`&HFF000000`) so the
  letters get no dark edge. Checked on ffmpeg 9.0.2 (gyan build): a
  wrapped text shows one even box, and a single line looks as before.
  Word captions keep their outlined style.
- `BorderStyle 4` needs libass 0.17 or newer; an older one would draw
  the text with no box. Nothing else renders these clips, so this is
  recorded here rather than checked at startup. Manual steps RV13 step 7
  and RV14 step 6.

## Amended by text styling (V7 item 9g, ADR 0031)

- Each text may have a colour (white, yellow, green, cyan, pink,
  orange), a look (`box`, the half-dark box, or `outline`, a black edge
  and no box), a size (`large`, the original, `medium` 0.8, `small`
  0.65), a 0.3 s fade in, and a position of its own: `x` and `y`,
  fractions of the frame for its centre, set by dragging it on the
  review page's live preview. This reverses decision 1's two places
  only and the "free placement" alternative, at the maintainer's ask;
  `place` stays for texts not dragged.
- `OnScreenText` gains `colour`, `look`, `size`, `fade`, `x`, `y`, all
  defaulting to the original look; the labels file writes them only when
  not the default, so older labels and unstyled texts are written as
  before. The default look adds no override tags, so its burn-in is
  unchanged.
- In the ASS: `\\an5\\pos` at the fraction of the canvas for a dragged
  text, `\\c`, `\\fs` and `\\fad(300,0)` per event, and a second
  `TextOutline` style (BorderStyle 1, black edge, the captions' outline
  width), added only when a text uses it, since libass cannot switch box
  and outline per event.
- Thumbnails keep their text and place only. Checked on v2924392099: a
  pink, outlined, medium, faded, dragged text burned where the preview
  showed it. Manual step RV22.

## Revisit when

- The ffmpeg build changes to one with an older libass (RV13 step 7).
- The maintainer wants other looks (colours, outlined text, all caps) or
  more places.
- The performance report shows hooks or thumbnails do or do not help.

## Related

- 0007, 0017 (captions), 0019 (manifest), 0024 (review page), 0027 and
  0028 (posts), 0029 (cuts).
