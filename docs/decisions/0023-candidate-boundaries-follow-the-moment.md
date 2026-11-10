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

## Amended by the V5 baseline (item 4)

Heuristic and LLM (`qwen3.5:9b`) top 10 on four trial VODs, every draft
judged in the review page, labels in `data/labels/picks/`. The gaps are
label minus candidate, from `eval` (0012). Ohnepixel (v2924046310) is left
out: its video started at 64 s, the player ran 64 s off while it was
judged, and its labels were moved by -64 s (0024 start offset amendment),
so most of its labels no longer sit on the drafts' own spans.

On the other three VODs, 31 keepers (13 heuristic, 18 LLM):

- **Ends.** 12 of 31 end early: the maintainer moved the end 2.4, 6.4, 8.1,
  10.9, 15.8, 17.0, 17.2, 18.2, 24.5, 29.2, 29.7 and 36.7 s later (median
  17.1 s). 17 are as cut or within 1 s. 2 end over 1 s earlier (-13.2 and
  -22.5 s), both from a whole clip moved earlier, not a trimmed end.
- **Starts.** 3 of 31 start earlier, by 8.3, 13.2 and 29.5 s; the last two
  are the same two whole-clip moves. No keeper starts later.
- **What this sets.** The extensions are far longer than the gap to the next
  speech after the last word (0 to 0.74 s on the first trial's drafts), so a
  pause of under a second would barely move an end. Item 5 chooses the pause
  length by trying lengths against these 12 ends, read from the
  transcripts, and records the choice in its own amendment. Decision 5
  stands: starts are dragged on 3 keepers against 12 ends, so starts stay.

## Amended by item 5: end extension, heuristic only

`extend_ends` in `adapters/detection/windows.py` runs after `select_top`.
Each chosen candidate's end moves word by word from its last word (a
segment's span where it has no words) and stops at the first word followed
by a gap of at least `PAUSE_SECONDS = 1.5`, or at the transcript's end. It
never goes past `MAX_CANDIDATE_SECONDS = 90.0` from the start, nor past the
start of a later chosen candidate. Starts do not change.

**The pause length.** Simulated on the 12 dragged ends above: at 1.5 s the
heuristic's early ends go from 6 to 1, at 1.0 s to 2, with the same 4 ends
over 5 s past the maintainer's in both. 1.5 s was chosen.

**Measured** with `eval` at top 10 against the judged labels on
v2922695332, v2917872953 and v2922589775, the same LLM replies for before
and after. "Early" is a keeper whose label ends over 1 s later; "over" is one
whose label ends over 5 s earlier.

| Detector | Keepers | Early | Over by >5 s | Over by |
|---|---|---|---|---|
| heuristic, before | 13 | 6 | 1 | 22.5 s |
| heuristic, after | 12 | 1 | 4 | 15.7, 17.0, 17.3, 36.9 s |
| LLM, before | 18 | 6 | 1 | 13.2 s |
| LLM, after | 18 | 6 | 7 | 10.8 to 49.5 s |

- The heuristic's lost keeper is the adapt draft at 1:59:19, which was on
  its `fix` label only by its exact draft span (0012); with a new end it is
  unjudged, not rejected. The 22.5 s and 13.2 s overs are whole clips the
  maintainer moved earlier, not dragged ends.
- The LLM's early ends stay early (3.0 to 36.7 s short after): its drafts
  already end where speech stops, and the moment goes on in silence. On
  stableronaldo four of its keepers run 37 to 49 s long, and three
  candidates reach 90 s.
- Clip-label recall at 10 and 30: 0 for both detectors on all three VODs,
  before and after.
- The heuristic's median candidate grows from about 42 to about 55 s; the
  LLM's 1 to 19 s candidates stay that short (decision 1, item 6).

**Decision.** Adopted for the heuristic, which fixes the costliest failure
(clips that end too early) at the price of three long ends that are trimmed
in the review page. Not adopted for the LLM, which it does not help
(decision 6). This amends decision 2: end extension is shared code that a
detector opts into, not a step every detector runs. Decision 7 holds:
`extend_ends` is a pure function of the transcript and the chosen
candidates.

## Revisit when

- The LLM is chosen as the `drafts` default (V5 item 11), or its early ends
  are fixed another way: measure end extension on it again.
- The baseline shows starts dragged as often as ends: extend starts too.
- Transcript pauses end clips in the wrong place often: try audio silence.
- A niche other than streamers is tuned, if its moments need another range.

## Related

- 0012: evaluation, and judged labels (V5 amendment)
- 0014: windows and the LLM detector
- 0015: determinism
- 0024: the review page, where the maintainer drags the ends
