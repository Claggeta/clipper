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

## Related

- 0013: detector choice on the CLI
- 0014: local LLM detector
