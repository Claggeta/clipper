# 0023: Candidate boundaries follow the moment

- Status: Accepted
- Date: 2026-11-09
- Amends: 0014 (decision 2: a candidate's bounds are no longer its window's)

## Context

Both detectors score the same windows (0014): whole segments inside 45 s,
stepped by 15 s (`sliding_windows`). A candidate is a window, so it starts at
its first segment's start and ends at its last segment's last word, and
`select_top` keeps the best-scored of overlapping windows without merging
them. The trial week (CLAUDE.md, 2026-11-09) showed what that costs:

- Of 11 drafts the maintainer would post, 6 needed more at the end: 4 of 5 on
  stableronaldo, 2 of 3 on ohnepixel (one stops after he opens the food,
  before he tries it). The next speech starts 0 to 0.74 s after the clip ends
  on 4 of the stableronaldo five, so most clips end where the talk carries
  straight on.
- Drafts 1 and 3 on stableronaldo are one moment, the gas pump, split into
  neighbouring windows 2.5 s apart.
- A window with little speech becomes a candidate only as long as that
  speech. The LLM's top candidate on v2917438534 was the single word
  "Strongest" (0.5 s), and 8 of its top 30 on v2922695332 were under 15 s.

The maintainer wants clip length to follow the moment, not a fixed window
(alignment interview, 2026-11-02), and set the range at 15 to 90 s
(2026-11-09).

## Decision

1. **Candidates are 15 to 90 s.** A window shorter than 15 s is not scored,
   by either detector. 90 s caps every extension and merge below. Both
   bounds are module constants beside `WINDOW_SECONDS`, not settings.
2. **Boundaries are shaped in shared code, the same for every detector.**
   Detectors still differ only in how they score windows (0014 decision 2
   stands for scoring). The steps, in order: windows are made and scored;
   neighbouring windows that belong to one moment are merged; the top N are
   selected; each chosen candidate's end is extended. The code stays in
   `adapters/detection/windows.py`.
3. **End extension.** A chosen candidate's end moves past its last word to
   the next pause in speech, never past 90 s in all and never into the next
   chosen candidate. A pause is a gap in the transcript's word or segment
   timestamps. How long a gap counts as a pause is not chosen here: it is set
   from the ends the maintainer drags to in the V5 baseline (0024, 0012
   amendment), and recorded in the item's amendment.
4. **Merging.** Neighbouring windows that both score near the top and sit
   close together become one candidate of up to 90 s. The rule (score band,
   gap) is decided in its own item, from the same baseline.
5. **Starts stay where they are** in V5. One trial draft lacked context at
   the start or the end, and the baseline's dragged starts decide whether
   starts move later.
6. **Each change is one PR, measured before and after** on the trial VODs at
   top 10 against the maintainer's judged labels (0012 amendment): postable
   drafts per VOD, drops, keepers cut short, seconds dragged at each end, and
   clip-label recall at 10 and 30 as a secondary check. Unjudged candidates
   are judged first. A change that does not help on those counts is recorded
   and not adopted, as in 0021.
7. The heuristic stays deterministic (0015): every step above is a pure
   function of the transcript and the scores.

## Alternatives considered

- **Fixed padding** (say 3 s after the last word). Simplest, but the trial
  ends needed different amounts, and a fixed pad can run into the next
  sentence. Kept as the fallback if pauses prove unreliable.
- **Silence from the audio** instead of transcript gaps. More accurate where
  Whisper's word times drift, but it is an audio feature with its own pass
  over the file. It can come with the loudness spike (V5) if transcript gaps
  fall short.
- **The LLM proposes its own boundaries** (0014's deferred alternative).
  Changes what `eval` compares and costs a long prompt per moment. Not now.
- **Longer windows** (say 90 s) instead of merging. Every candidate gets
  long, including moments that are 20 s. Rejected.

## Consequences

- (+) Fixes the measured failures for both detectors in one place.
- (+) The boundary rules are set from the maintainer's own drags, not
  guessed.
- (-) A candidate no longer equals a window, so analyses made before and
  after a change differ in their spans, and old rendered drafts stay as cut.
- (-) Longer candidates hit labels more easily under the half-of-the-shorter
  rule (0012), so clip-label recall can rise without a better pick. That is
  why it is the secondary check.
- (-) Transcript gaps miss moments where speech carries on over the action
  (talking while the food is tried). Those need audio or visual signals.

## Revisit when

- The baseline shows starts dragged as often as ends: extend starts too.
- Transcript pauses end clips in the wrong place often: try audio silence.
- A niche other than streamers is tuned, if its moments need another range.

## Related

- 0012: evaluation, and judged labels (V5 amendment)
- 0014: windows and the LLM detector
- 0015: determinism
- 0024: the review page, where the maintainer drags the ends
