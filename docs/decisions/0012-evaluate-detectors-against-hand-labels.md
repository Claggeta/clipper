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

## Related

- 0013: detector choice on the CLI
- 0014: local LLM detector
