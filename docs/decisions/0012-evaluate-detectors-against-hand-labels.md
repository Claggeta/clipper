# 0012: Evaluate detectors against hand-labeled moments

- Status: Accepted
- Date: 2026-11-01

## Context

The heuristic detector (M3) has never been measured against anything. The only
real run, on one 5:14 speech, produced 5 candidates scored 0.26 to 0.03, and
nobody has said whether they are the right 5. V3 wants to compare detectors,
including a local LLM, and a comparison needs a reference to compare against.

The long-term reference is platform performance data. Clipper has posted
nothing and has no accounts with metrics, so that data does not exist yet.
Retention and watch time are private to the account owner, so they are not
available for other people's clips at all. What exists today is the
maintainer's own judgment of which moments in a video are worth cutting.

## Decision

1. The first reference is a set of hand-written labels: for each video, the
   moments the maintainer would cut. A label is a time range in seconds with an
   optional note. One JSON file per video, naming the source video the way
   `AnalysisResult.source` does (the file name).
2. A new domain model holds a labeled video, validated like the existing
   models. Labels live outside the analysis JSON, so re-running `analyze` never
   overwrites them. Their location is a setting, `CLIPPER_LABELS_DIR`, default
   `data/labels`.
3. A pure function compares a detector's candidates with a video's labels and
   returns counts. `clipper eval` is the command that runs it: it takes a saved
   analysis JSON and a labels file, runs the chosen detector over the **saved
   transcript** (no re-transcription), and prints the result. Because the
   transcript is saved, comparing detectors on the same video is cheap.
4. Initial metric, deliberately simple: a candidate **hits** a label when their
   overlap is at least half of the shorter of the two ranges. Report, per video,
   labels covered by at least one candidate (recall) and candidates that hit at
   least one label (precision), with the counts and not only the ratios. The
   half-overlap threshold is a first guess. It has not been tried on real
   labels and is expected to change.
5. Results are reported as indicative. With a handful of videos and one
   labeler, a difference between detectors is a sanity check, not a measured
   improvement. Every report states the number of videos and labels it covers.

## Alternatives considered

- **Wait for platform metrics.** Rejected. It blocks all V3 work on posting and
  analytics, which are later milestones, and the data would arrive slowly.
- **Public replay data (YouTube "Most replayed").** Not rejected, deferred. It
  may be extractable, but that is unverified, and its terms have not been
  checked. It is parked as a later second label source.
- **Judge by eye each time, with no stored labels.** Rejected. Nothing is
  repeatable, and a detector change cannot be compared with the last one.
- **A labeling UI or a keep/reject helper command.** Out of scope for V3. Labels
  are written by hand first.

## Consequences

- (+) Any detector change can be compared with the last on the same labels,
  without Whisper or ffmpeg.
- (+) Labels are plain files the maintainer can read and correct.
- (-) One labeler's taste, and few videos. The numbers cannot support a claim
  about audiences.
- (-) The maintainer must collect videos and write labels before `eval` can say
  anything about real media. No real labels exist yet.
- (-) Overlap thresholds are guesses until there is something to tune them on.

## Revisit when

- Posted clips have metrics. Platform data becomes a second reference and may
  replace hand labels as the main one.
- The metric disagrees with what the maintainer sees when comparing two
  detectors by eye.

## Amended by the label model PR (V3 item 2)

- A label file is `<CLIPPER_LABELS_DIR>/<video stem>.json` by convention. It
  holds `source` (the video's file name, as in `AnalysisResult.source`) and
  `labels`, a list of `{"start", "end", "note"}`, `note` optional. The
  `source` field ties the file to a video, not the file name.
- Times are written as `"M:SS"` or `"H:MM:SS"` strings, with optional
  fractional seconds (`"0:49.78"`), and held as seconds in the model. Plain
  numbers are rejected. This refines "a time range in seconds" in decision 1:
  the first labels were written as `0.50` meaning 0:50, which as a number of
  seconds loads silently as half a second.
- `end` must be after `start`. Labels must be in order and must not overlap;
  touching is allowed, as for transcript segments. Unknown keys are rejected,
  so a typo fails instead of being ignored. An empty `labels` list is valid: it
  records that nothing in the video is worth cutting.
- The model does not check labels against the video's length. It cannot know
  it. `eval` can, once it has the transcript (item 3).
- The models are `Label` and `LabeledVideo` in `domain/models.py`, loaded by
  `load_labels` in `adapters/results/labels_json.py`, which raises
  `LabelsError`.
- Label files are committed to git: `.gitignore` ignores `data/*` except
  `data/labels/`. They are the only hand-made data in the project, while
  videos, transcripts and analyses can be fetched or regenerated.
- First labels: on `data/test_input.mp4` the maintainer labeled four moments
  that together cover the whole speech. Labels like that cannot count anything
  against a detector, since every candidate lands inside one. A video with
  stretches the maintainer would not post is needed before `eval` can tell
  detectors apart.

## Amended by the eval PR (V3 item 3)

- The comparison is `evaluate(labeled, candidates)` in `core/evaluation.py`.
  It returns an `Evaluation` (`domain/models.py`) holding the labels, the
  candidates and every hitting `(label, candidate)` pair. Recall and precision
  are derived from the pairs.
- The hit rule is decision 4's, with `HIT_FRACTION = 0.5` as a module constant,
  not a setting or a flag, so it is not tuned on one video before a long one is
  labeled. Times are rounded to milliseconds before comparing. Touching ranges
  never hit.
- `clipper eval <analysis.json> [labels.json]`. The labels path defaults to
  `<CLIPPER_LABELS_DIR>/<stem of the analysis source>.json`, the item-2
  convention. One video per run. Combining several videos into one report is
  decided when a second label file exists.
- `labels.source` must match `AnalysisResult.source`, compared like
  `check_source_matches` (`check_labels_match`, raising `LabelsError`).
- Before item 4, `eval` runs `HeuristicClipDetector(top_n=CLIPPER_TOP_N)` on
  the saved transcript, built the same way `analyze` builds it.
- A label that ends after the transcript's last segment is noted, not
  rejected, and stays in the recall denominator. A moment the maintainer wanted
  and no detector can reach is a miss, and a better transcript (item 5) can
  then show up as higher recall. `eval` does not probe the video, so "past the
  end" means past the transcript. On `test_input.mp4` the last label ends
  1.2 s after the transcript.
- With no labels, recall is reported as `n/a`, and precision is 0 of the
  candidates. With no candidates, precision is `n/a`. Both exit 0.

## Amended by the first long-video labels

- A label file may list only some of the moments worth cutting. The labels for
  `oFbD0Ot7nQ8.mp4` (a long stream; the last label ends at 4:51:00) are the
  maintainer's main clips, and the maintainer said they are not all the
  clippable moments.
- With incomplete labels, a candidate on a good but unlisted moment counts as
  a miss, so precision is a lower bound. Recall is unaffected: it asks whether
  the listed moments were found. When comparing detectors on such a file,
  read recall first.
- `top_n` caps recall: with 30 labels and the default of 5 candidates, at most
  5 labels can be hit. Set `CLIPPER_TOP_N` on the `eval` command to compare at
  a larger N.
- Whether a label file is complete is not recorded in the file. Add a field
  for it if complete and incomplete files ever need to be told apart.

## Amended by the hit-rule review (V3 item 6a)

- On `oFbD0Ot7nQ8.json` at top 30, three of the seven candidates that hit a
  label were 45 s candidates inside the one 9.5-minute label (label 16), so
  precision counted one moment three times.
- The pair rule (decision 4) and `HIT_FRACTION = 0.5` are kept. Measured on
  both saved analyses with the heuristic: on the long video, 0.3 and 0.5 gave
  the same recall and precision at top 5 and top 30, and 0.7 lowered recall
  from 5 to 3 at top 30. Every hit outside label 16 overlapped by at least 66%
  of the shorter range, and the nearest miss by 29%. Nothing here supports a
  different threshold.
- Rules measured against the label's length (IoU, or overlap as a fraction of
  the label) are rejected for now. Candidates are fixed 45 s windows and 7 of
  the 30 long-video labels are longer than 90 s (up to 574 s), so those rules
  measure label length against window length. At 0.5 they cut top-30 recall
  from 5 to 2 (IoU) or 3 (fraction of the label).
- Precision is now one-to-one. `evaluate` walks the candidates in rank order,
  and each claims the earliest label it hits that no higher-ranked candidate
  has claimed. Those pairs are `Evaluation.matches`, and precision is matched
  candidates over all candidates. A candidate that hits only claimed labels is
  a **repeat**, not a hit: the maintainer would not post a second clip of a
  moment already matched. Recall still counts labels with any hit.
- Matching is greedy, not a maximum matching. They can differ only when one
  candidate hits two short neighbouring labels and a later candidate could
  only have taken the first. This has not happened in either label file.
- `eval` marks a repeat in the candidates table as `16 (repeat of #3)` and
  ends the precision line with `; N more repeat a matched label`. The labels
  table still lists every candidate that hit each label.
- Effect on the saved analyses (the maintainer's labels, heuristic, recall
  unchanged throughout): `test_input.json` precision went from 5 of 5 to 3 of 5
  (2 repeats); `oFbD0Ot7nQ8.json` from 3 of 5 to 2 of 5 at top 5 (1 repeat),
  and from 7 of 30 to 5 of 30 at top 30 (2 repeats).
- Labels longer than two windows stay unreachable as whole moments. Proposing
  longer clips is a detector question (item 7 or later), not a metric one.

## Amended by labels from Twitch community clips

- **A second label source: Twitch community clips.** Viewers clip the moments
  they react to. The maintainer collects, by hand, each clip's link and its
  "Watch full video" link, a VOD URL with `?t=`, into a links file, one clip
  per line: `<clip url> <vod url>`, `#` comments. `clipper labels <links
  file>` turns it into an ordinary labels file,
  `<CLIPPER_LABELS_DIR>/v<id>.json` with `source` `v<id>.mp4`. Every line must
  name the same VOD. Nothing else changes: `eval` reads the file like a
  hand-written one.
- **Span rule: `t - length` to `t`.** The clip's length and title come from
  yt-dlp's clip metadata, with no download. That metadata has no VOD ID or
  offset, so the link's `t` is the only position. Measured on 4 clips
  (2026-11-03 and 2026-11-09) by matching each clip's frames against a
  downloaded VOD section (4 fps, 64x36 grey, mean absolute difference, a clear
  minimum each time): `t` was 0, 0.5, 0.75 and 3.75 s before the clip's real
  end, never after it. So `t` marks the end to within 4 s, and a label may sit
  up to 4 s early. The 3.75 s case was confirmed against a second, differently
  cut section; its cause is unknown. Twitch's API calls its `vod_offset` the
  start, which is a different number. The error is not corrected or padded:
  a 30 s label 4 s early still overlaps its moment by 26 s, a hit needs half
  the shorter range, and padding would lengthen labels and make them easier to
  hit. A start before 0 is clamped to 0.
- **Rejected ways to place a clip**: the clip's creation time minus its length
  (18 s late on the one clip tried), and the Twitch API (a registered app and
  a token, for a number the link already gives). Not reopened.
- **Merge rule.** Overlapping spans, in time order, merge into one label,
  transitively, so a chain of overlapping clips becomes one long label.
  Touching spans stay separate, as touching labels are allowed. The note lists
  the merged clips' titles (`Twitch clips: <title>; <title>`). A repeated clip
  link is an error, as a likely pasting mistake. The command prints clips
  read, labels after merging, minutes covered and the path, so a long merged
  label shows.
- **An existing labels file is not overwritten** without `--force`, since
  label files are the project's hand-made data. Links files live in
  `data/labels/links/` and are committed with the labels, like them.
- **Caveats.**
  - Clips are biased toward popular moments and say nothing about the rest,
    so the labels are incomplete: precision is a lower bound and recall is
    read first (the long-video amendment above).
  - Audience reaction is not the same as what the maintainer would post. The
    labels measure "did the detector find what viewers clipped", not "did it
    find the maintainer's clips".
  - Clips cannot be both a detector input and the labels for the same videos.
    If a detector ever uses clips or other audience signals, it is evaluated
    on videos whose clips it did not see.
  - Reading clip metadata with yt-dlp falls under ADR 0020's accepted risk
    (its amendment for clip lookups).
- **First real run** (2026-11-09, yt-dlp 2026.10.26, manual steps T0 to T6,
  run by the assistant in a child process): 5 clips the maintainer collected
  from VOD 2917438534 (lacy, 2 Nov 2026, 5:15:42) were looked up in 4.5 s and
  gave 5 labels of 24 to 32 s, none merged, 2.4 minutes in all. `eval` with
  the heuristic on `v2917438534.json`: recall 0 of 5 and precision 0 of 5 at
  top 5; recall 1 of 5 and precision 1 of 30 at top 30 (candidate 16 on
  label 2, no repeats). The top candidate, 4:38:02-4:38:38, overlaps label 4
  (4:37:36-4:38:06) by 4 s, short of the 15 s a hit needs; shifting the label
  by the up-to-4 s error would not reach it either. Five clips are a small
  sample, and the transcript was cached from an earlier run.
- The LLM detector on the same labels (`llm:qwen3.5:9b`, 2026-11-09): recall
  0 of 5 and precision 0 of 5 at top 5 (822 s), and 0 of 5 and 0 of 30 at top
  30 (not timed). Its nearest candidates start about 2 minutes before label
  3. Its scores cluster as before: 15 of the top 30 are 0.85. Its top
  candidate is a 0.5 s window holding one word, "Strongest", scored 1.00.
- Second VOD, 2922695332 (jasontheween, 6:31:25 by the transcript, fetched
  and analysed by `drafts` on 2026-11-09): 4 clips the maintainer collected
  gave 4 labels of 17 to 60 s, 2.3 minutes. Recall 0 of 4 for both detectors
  at top 5 and top 30, so precision 0. The heuristic ran at each N; the LLM
  ran once at top 30 (909 s), and its top 5 are its first 5 rows, since
  `select_top` picks greedily in score order. Two of the clips have almost no
  speech in the transcript ("Oh, my god." in 30 s, and nothing in 17 s), and
  one has 23 broken-up words in 60 s: moments a transcript-only detector
  cannot see. The nearest miss is the LLM's candidate 18, 5:38:25-5:38:49,
  which starts 1 s after label 4 ends. Over both VODs, 9 clips: the heuristic
  found 1 at top 30, the LLM none.
- Third VOD, 2917872953 (stableronaldo, 5:03:36 by the transcript, `drafts`
  on 2026-11-09): 5 clips gave 5 labels of 20 to 60 s, 2.7 minutes. Recall 0
  of 5 for both detectors at top 5 and top 30 (the LLM once at top 30, 990
  s). Yet the maintainer would post all 5 of the heuristic's drafts, and the
  LLM's second candidate is one of them. Here the clip labels miss every
  moment the maintainer chose: precision against clips is a lower bound, and
  on this VOD a loose one. Over three VODs, 14 clips: the heuristic found 1
  at top 30, the LLM none.
- Fourth VOD, 2922589775 (adapt, 5:18:27 by the transcript, `drafts` on
  2026-11-09): 5 clips gave 5 labels of 12 to 30 s, 2.2 minutes. Recall 0 of
  5 for both detectors at top 5 and top 30 (the LLM once at top 30, 1,168 s,
  every score 0.85 to 0.95). The maintainer would post 1 of the 5 heuristic
  drafts, and it hits no clip. Over four VODs, 19 clips: the heuristic found
  1 at top 30, the LLM none.
- Fifth VOD, 2924046310 (ohnepixel, 4:03:00 by the transcript, `drafts` on
  2026-11-09): 6 clips gave 6 labels of 18 to 60 s, 3.2 minutes. Heuristic:
  recall 1 of 6 at top 5 (precision 1 of 5) and 2 of 6 at top 30 (precision
  2 of 30). LLM, once at top 30 (707 s): 0 of 6 in its top 5, 1 of 6 at top
  30 (precision 1 of 30). The heuristic's top-5 hit is a draft the
  maintainer would not post as cut (no context); the 3 drafts they would
  post hit no clip. Over five VODs, 25 clips: the heuristic found 1 at top 5
  and 3 at top 30, the LLM 1 at top 30.

## Amended by the V5 scope (labels from judged drafts)

Over the trial week the maintainer would post 11 of 20 heuristic drafts on
four VODs, and none of the 11 overlaps a Twitch clip. Clip labels are a weak
stand-in for "worth posting"; the maintainer's own verdicts are the reference
V5 measures picking against.

- **A third label source: judged drafts.** The maintainer judges drafts in
  the review page (0024). Each judgment is `post` (as cut), `fix` (with the
  span the maintainer set) or a drop. `post` and `fix` are labels with an
  optional note; drops are rejected spans. A `fix` label keeps the span the
  draft had, so the seconds dragged at each end can be reported.
- **Kept apart from clip labels**: judged labels live in
  `data/labels/picks/<stem>.json`, chosen with `CLIPPER_LABELS_DIR` (in a
  child process) or the labels path, so one file never mixes sources.
- **Pooled judging.** A judged-labels file holds verdicts on drafts from any
  detector or variant: the union of each one's top 10 on the trial VODs. A
  candidate is judged once; a new variant adds only the candidates nobody
  judged yet. Labels from one detector's drafts alone would give that
  detector full marks by construction.
- **What `eval` reports on a judged-labels file**: candidates on `post` or
  `fix` labels, candidates on rejected spans, and **unjudged** candidates,
  on neither. Unjudged candidates are judged before two variants are
  compared, since today any non-hit counts as a miss. Also reported: keepers
  cut short (a candidate ending before its label's end) and the seconds
  dragged at each end.
- **Unchanged**: the hit rule (`HIT_FRACTION = 0.5`), one-to-one precision,
  and clip labels as a secondary check on the same VODs.
- **Caveats.** Verdicts exist only near what some detector proposed (Add
  clip reaches 2 minutes either side of a draft, 0024), so recall against
  judged labels is relative to the pool, not to the whole VOD. One labeler,
  a handful of VODs: results are counts per VOD, read as indicative.
- The file format, how a candidate hits a rejected span, and the tolerance
  for "cut short" are decided in V5 item 2.

## Amended by the judged labels PR (V5 item 2)

- **Format.** The same labels file, with optional fields, so existing files
  load unchanged:

  ```json
  {
    "source": "v2917872953.mp4",
    "labels": [
      {"start": "1:39:11", "end": "1:39:55", "verdict": "fix",
       "draft": {"start": "1:39:11", "end": "1:39:49"}, "note": "..."},
      {"start": "2:10:00", "end": "2:10:40", "verdict": "added"},
      {"start": "5:24:04", "end": "5:24:49", "verdict": "post"}
    ],
    "rejected": [{"start": "0:12:00", "end": "0:12:45", "note": "..."}]
  }
  ```

- **Three verdicts.** `post` is a draft as cut. `fix` is a draft whose span
  was moved; `draft` holds the span it had, is required, and must differ from
  the label's span. `added` is a moment no draft proposed (Add clip, 0024).
  Only `fix` has a `draft`.
- **Judged or not, never mixed.** A file has a verdict on every label or on
  none. Rejected spans need verdicts, so a clip-labels file cannot gain
  them by accident. A judged file is one with a verdict or a rejected span
  (`LabeledVideo.judged`).
- **Rejected spans** are ordered by start and may overlap each other (two
  variants' drafts of one dropped moment) and the labels (the dropped half
  of a split moment). Labels still may not overlap each other: a later
  variant's draft on a kept moment is judged through that label.
- **Classification.** With the same hit rule, each candidate is on a label
  (matched or a repeat, as before), else **rejected** if it hits a rejected
  span, else **unjudged**. A label wins over a rejected span. Files without
  verdicts report exactly as before.
- **Gaps, not seconds dragged.** A `fix` label's drag describes the draft
  that was shown, so it cannot say where a changed detector's candidate
  ends. `eval` reports, for each candidate matched to a judged label, the
  **gap**: label start minus candidate start, and label end minus candidate
  end (`boundary_gaps` in `core/evaluation.py`). For the run that made the
  draft it equals the drag; after a boundary change (0023) it is the
  before-and-after measure. `draft` stays in the file as the record of what
  was shown.
- **Cut short.** A keeper **ends early** when its end gap is over
  `CUT_SHORT_SECONDS = 1.0`, a module constant beside `HIT_FRACTION`, and
  **starts late** when its label starts more than 1 s before it. A drag on
  a slider is good to about half a second, and the trial asked for "a couple
  of seconds" more. "As cut" is neither.
- **Report**, for judged files only: a Verdict column in the labels table;
  `rejected` or `unjudged` in place of `-` in the candidates table; then

  ```
  Judged: 6 on a keeper (4 as cut, 2 end early, 0 start late), 1 repeat, 3 rejected, 1 unjudged
  End gap (label - candidate): median +0.8 s, -0.5 to +6.2 s over 6 keepers
  Start gap (label - candidate): median +0.0 s, -1.0 to +0.3 s over 6 keepers
  Note: 1 unjudged candidate; judge them before comparing variants
  ```

  (illustrative numbers). Recall and precision lines are unchanged; recall
  on a judged file is relative to the pool.
- No new setting or option: a judged file is chosen by the labels path or
  `CLIPPER_LABELS_DIR=data/labels/picks`. `save_labels` writes the new
  fields, for the review page (0024). Nothing writes verdicts yet.

## Amended by the draft span fix (V5 item 4)

The baseline's first `eval` runs counted a judged draft as unjudged. On
v2922589775 the maintainer fixed heuristic draft 8 (1:59:19-1:59:54) to
1:58:50-1:59:32. The two overlap by 13 s of the shorter 35 s, under
`HIT_FRACTION`, so the draft hit no label, although the label's `draft`
names it.

- A candidate also **hits** a `fix` label when its span equals the label's
  `draft` to the millisecond (`is_draft_of` in `core/evaluation.py`), the
  same match the review page uses to find a clip's own judgment (0024).
  Matching in rank order, repeats, recall and the gaps are unchanged; the
  gap is then the whole move, here -29.5 s at the start and -22.5 s at the
  end.
- Only the exact span counts. A later detector's candidate near the draft
  still has to meet `HIT_FRACTION`, since the maintainer judged the draft,
  not what lies near it.
- `post` labels and rejected spans carry no draft, so they still match by
  overlap only. A `post` label equals its draft, so the rule changes nothing
  for it.

## Amended by the style switch (ADR 0025, V5 item 13e)

- A judged label may carry `style`, `fit` or `face`: the vertical style the
  maintainer kept for the clip in the review page. Absent on labels from
  clips without styles and on older files. `eval` does not read it; it is
  there to count which style wins.

## Related

- 0013: detector choice on the CLI
- 0014: local LLM detector
