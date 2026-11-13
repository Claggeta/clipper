# 0033: Summarize a VOD's story in beats

- Status: Accepted
- Date: 2026-11-13
- Amends: 0014 (the shared Ollama client), 0019 (manifest), 0024 (review
  page), 0028 (post queue, in the last PR)

## Context

The maintainer plans Shorts from streams of incremental games, which
start with a tiny, boring task and work toward a huge goal through
upgrades. They asked for a Summarize button in `review` that tells the
episode's story, not one that joins the kept clips (that feature became
Combine, ADR 0032 amendment). The drafts are picked as moments that
stand alone (0014, 0023), not to cover a progression, so the story needs
its own picking.

## The spike (2026-11-13)

A throwaway script on two Sonoket Gaming VODs, judged by the maintainer:
DQP3pmyJX4o (That Damn Grass Pt. 1, 80 min, 7,986 words) and 4C6poRImasE
(Moon Sweepers Part 1, 2:45:33). The local model (`qwen3.5:9b`) wrote a
note per 3-minute chunk (what happens, its kind: start, goal, progress,
milestone, setback, ending, other; its importance; the key line and its
time), then picked the beats from the notes; spans were cut around each
key line and the beats rendered and joined with cards.

- Round 1 (6 beats, 8 to 14 s, 62 s in all): the notes read as a recap
  and the beats came in story order, but the maintainer found it rushed
  ("the clips used could have been extended a bit longer"); it skipped
  the middle and one card promised what its clip did not show.
- Round 2: beats 15 to 25 s from the start of the thought to a pause or
  sentence end, 8 to 10 beats spread across the stream with a setback,
  the whole kept to 170 s; 8 beats, 132 s, with the struggles and the
  payoff. Told to say only what the line says, the cards quoted it,
  swearing included.
- Round 3: cards in the model's own words, no quoting or swearing; a
  beat too short because the next segment was long takes that segment's
  words up to a break. That Damn Grass: 9 beats, 161 s, the whole arc
  from nail clippers to stopping at max goats. On Moon Sweepers the story
  started at 1:47:10 and the cards were the notes' kinds: the 5,370-token
  story prompt was longer than Ollama's default context, which drops the
  start of a prompt silently. With `num_ctx` 16384: 8 beats, 148 s, from
  the toothbrush to "automation takes forever", one 50-minute gap.
- Time: notes 41 to 96 s, the story 5 to 11 s, rendering about half real
  time; 1.5 to 2.5 minutes per VOD.
- The maintainer: "Those sound good to me."

## Decision

1. **Picking, in `core/story.py`**: the transcript in chunks of
   `CHUNK_SECONDS = 180`; a note per chunk; the beats from the notes in
   one request (8 to 10, in time order, spread across the stream, the
   start and the ending, a setback if there is one, a card of at most 6
   words in the model's own words, no quoting or swearing, its trailing
   full stop removed).
2. **Spans, in core**: from the key line back to the start of the
   thought (segments that run into it, up to 6 s before), on to
   `MIN_BEAT = 15` s and further to a pause of 0.8 s or a sentence end,
   at most `MAX_BEAT = 25` s, taking a long next segment's words up to a
   break when still short; beats that overlap merge; the least important
   middle beats are dropped to fit `STORY_LIMIT = 170` s.
3. **The model** behind a `StoryWriter` Protocol (a note per chunk, the
   beats from the notes) with an Ollama adapter on the shared client.
   The client takes an optional context size, used for the beats
   request (`num_ctx` 16384), and fails loudly (`LlmError`) when Ollama
   read fewer prompt tokens than were sent, so a prompt cut short can
   never pass as an answer. Detection and titles keep the defaults.
4. **In `review`**: Summarize video starts the plan in the background
   with progress; the beats come back for editing (play, start and end,
   card, remove, add at the playhead, music, the total with a warning
   past 180 s); the plan is kept in the manifest before rendering; Make
   summary cuts each beat from the source in the folder's framing (fit,
   with captions) and joins them with the cards and music by Combine's
   join (0032). The result is `<stem>_summary.mp4`, with a Summary entry
   to play, title (Suggest), rate, thumbnail, re-plan, remake or trash.
5. **Stored** as `RenderManifest.story`: the beats, the music, the file,
   the length, the title, rating and thumbnail. No judged label.
6. **Posting**: queued when rated, as `summary`, and recorded by
   `posted` by title or file (0028), in the last PR.
7. **In four PRs**: this ADR; the core and the adapter; the page, server
   and render with a manual step; the queue.

## Alternatives considered

- **Joining the drafts (Combine)**: the moments that stand alone, not
  the story; kept as its own feature.
- **Raising the context window for every request**: more memory and
  time for the many short detector and title requests, which fit.
- **A hosted model**: costs money, against the maintainer's rule.

## Consequences

- It sees words, not pictures: progress shown only on screen is missed,
  and a long stretch can be skipped; the beat editor is the answer.
- Captions show swearing as spoken, as in every draft.
- Beats render in the fit style only; no face style, zooms or per-beat
  audio, though the whole may have music.

## Revisit when

- Summaries of other niches (podcasts, talks) are wanted.
- The judged summaries show a picking pattern worth measuring.

## Related

- 0014, 0018, 0019, 0023, 0024, 0028, 0032.
