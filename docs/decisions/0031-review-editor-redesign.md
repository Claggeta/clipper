# 0031: The review page becomes a one-screen editor

- Status: Accepted
- Date: 2026-11-13
- Amends: 0024 (review page); later items amend 0017 (captions), 0025
  (face style), 0029 (cuts) and 0030 (on-screen text)

## Context

The maintainer reviews drafts every day in `clipper review` (0024), which
has grown item by item: the source player, step buttons, the film strip
with handles and cuts, the draft player with its style buttons, then the
title, notes, rating, people, on-screen text and thumbnail form, stacked
in one column. Asked on 2026-11-13 what slows review down, the
maintainer picked all four answers offered:

- **Layout and scrolling**: watching, trimming and the form are a screen
  apart.
- **Trimming precision**: hard to land a start, end or cut exactly.
- **Speed per clip**: too many clicks to judge one clip and move on.
- **Seeing the result**: the finished Short is only seen after Save
  re-renders it.

And wanted all four editing options offered on most clips: caption
editing, text styling, zoom and reframing, and audio. A full redesign
was chosen over polishing in place, still without a framework, and the
work goes before the finer LLM score (V7 item 5). The maintainer also
asked for a chat box that turns a described edit into the page's edits.

## Decision

1. **One screen, four regions**, at 1600x900 and up with no page scroll:
   - a clip list on the left, collapsible, with verdict, rating and
     length per clip;
   - a large 9:16 preview in the centre (the finished Short, see 4), with
     transport below it;
   - an inspector on the right with tabs: **Clip** (title, notes,
     rating, people, Save, Trash), **Captions**, **Text**, **Frame**
     (style, zoom, crop, thumbnail), **Audio**, and later **Assist** (9);
   - a timeline across the bottom: the film strip with span handles,
     then tracks for cuts, texts, zooms and audio, under one playhead
     and one zoom.

   The source player stays, smaller, in the Frame tab and behind a
   toggle, for looking outside the span. A layout mock is in
   `docs/mockups/review-editor.html`.
2. **Plain HTML, CSS and JavaScript, no build step and no new
   dependencies**, as 0024. The page is split into a few script files by
   region as it grows, loaded in order, with no modules bundler.
3. **Keyboard first.** Every frequent action has a key (play, J/K/L,
   frame step, set start and end, cut in and out, rate, Save, Trash,
   next and previous clip), Save and Trash may move on to the next clip,
   and `?` lists the keys. Keys are ignored while typing in a box.
4. **A live preview.** The centre player plays the source through the
   span and cuts, framed as the chosen style (fit as a scaled copy over a
   blurred one, face from the cached face track), with word captions,
   on-screen text and zooms drawn by the browser. It is close, not
   exact (browser fonts and filters, not libass and ffmpeg); Save's
   re-render stays the exact result, and the rendered draft can be shown
   in its place.
5. **Precision aids**: an audio waveform on the timeline from a peaks
   file ffmpeg makes once per video (cached beside the frames), frame
   stepping, handles and cuts that snap to word edges from the
   transcript (a key turns snapping off), and timeline zoom below one
   second per tile.
6. **New edits are stored like the old ones**: on the judged label and
   the manifest clip, checked by the server, and a change re-renders on
   Save through the one encode (0007). Each lands with its own
   amendment, which settles its fields:
   - caption fixes: a word's text replaced, its time kept; key words
     marked for a highlight, and the highlight colour per clip (0017);
   - text style: colour, box or outline, size, fade-in, and a free
     position dragged on the preview in place of `top` and `middle`
     only (0030; reverses its "free placement" alternative);
   - zooms: punch-ins over a time range at a chosen scale and centre,
     and a crop position set by hand for a stretch of a fit or face clip
     (0016, 0025);
   - audio: a volume change per clip, a music track under it, lowered
     under speech, and sound effects at a moment, from files the
     maintainer puts in `data/music/` and `data/sfx/` (not committed,
     tracks the maintainer may use on YouTube); nothing is downloaded or
     bought.
7. **Nothing else moves.** Spans, verdicts, cuts, ratings, people,
   titles, texts, thumbnails and styles keep their meaning and files;
   `eval`, `queue` and `posted` are unchanged. The layout PR changes no
   behaviour.
8. **Order**, one PR each with a manual step (V7 item 9): layout, keys,
   precision, live preview, caption editing, text styling, zoom and
   reframing, audio, then the assistant.
9. **An assistant last, measured first.** A chat box in an Assist tab
   sends the request, the clip's transcript with word times and the
   current edits to the local LLM (Ollama, `CLIPPER_LLM_MODEL`, through
   the shared client, 0014), which replies with a list of edits from a
   fixed set the page already has, as JSON against a schema. The page
   applies them as unsaved edits for the maintainer to check, then Save
   or Reset; it never saves or renders by itself. It sees words, not
   pictures, so requests about what is seen are out of reach. Local
   only, no hosted model. Built only if a spike on requests written by
   the maintainer finds the edits right often enough; may end in
   nothing.

## Alternatives considered

- **Polish in place**: keeps the stacked column, which is the main
  complaint. The maintainer chose the redesign.
- **A framework or build step** (React, Svelte, a bundler): easier state
  handling, but a toolchain and dependencies for a one-person local
  page, against 0024. Rejected; revisit if the plain scripts become hard
  to change.
- **A desktop editor or an NLE export** (an EDL for DaVinci Resolve):
  leaves Clipper's one-encode path and the judged labels behind.
- **A hosted model for the assistant**: costs money, against the
  maintainer's rule. Rejected.

## Consequences

- The page grows the most of any part of Clipper, and is still checked
  by manual steps, not tests; server-side checks keep unit tests.
- The live preview can differ a little from the burn-in; the manual steps
  compare them.
- Clips saved before an edit type exists simply lack its fields.

## Revisit when

- The plain scripts become hard to change (a framework, by amendment).
- The assistant spike says how reliable the local model is.

## Related

- 0007 (one encode), 0014 (Ollama), 0016 and 0025 (framing), 0017
  (captions), 0024 (review page), 0029 (cuts), 0030 (on-screen text).
