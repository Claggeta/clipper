# Clipper Vision

## Problem
Watching hours of long-form video and manually cutting clips is too much
of a time sink.

## Vision
A modular platform that generates ready-to-publish clips from long-form
content. Over time it learns from performance data and generates better
clips. That part is long-term: it needs accounts and posted clips, and
neither exists yet.

## Users
V1 user: the developer, running locally. No other users are assumed. As of
2026-11-02 the developer has no accounts and is not posting yet; the target is
4 to 10 long videos a week.

## V1 Scope
1. Input: a local video file.
2. Output: a transcript and the top 5 clip-worthy timestamps, each with a
   score and a reason.
3. Interface: a command-line tool that prints a readable table and saves
   JSON.

## Non-goals (V1)
- Clip rendering
- Captions
- Uploads
- Analytics
- Web UI
- Database
- User accounts
- ML training

## V2 Scope
Input: the saved `AnalysisResult` JSON and the original video. Output: one
rendered video file per clip-worthy candidate, optionally with captions
burned in. Interface: a `clipper render` command.

Non-goals for V2: vertical reframing or cropping, caption styling beyond one
default, word-level captions, hardware encoders, concatenating clips, uploads,
analytics, web UI, database, user accounts, ML training, and any change to
the detector.

## V3 Scope
Input: saved `AnalysisResult` JSON and a hand-written labels file per video.
Output: a measured comparison of detectors, and a second detector that scores
the heuristic's windows with a local LLM. Interface: `clipper eval`, and
`--detector` on `analyze` and `eval`.

Non-goals for V3: downloading or URL ingestion, posting, analytics or platform
metrics, a vector database or retrieval, a trained neural network, audio or
visual features, a labeling UI, a rename of the project, and any change to
`render`. Campaign briefs as a detector input are unscheduled and need their
own ADR.

V3 closes with `v0.3.0`. The Whisper experiment moved to V4 as GPU
transcription, for speed.

## V4 Scope
Input: a video URL (YouTube or Twitch) or a local file. Output: a folder per
video of post-ready drafts: 9:16 vertical (whole frame, blurred fill), styled
word-by-word captions burned in, and a suggested hook/title per clip. The
developer picks which to post, by hand. Interface: `clipper fetch`, changes to
`render`, and one command from a URL to that folder.

Non-goals for V4: posting or uploads, analytics or platform metrics, campaign
briefs, any change to which moments are picked (detectors, windows,
boundaries), audio or visual detection features, subject or face tracking for
the crop, hosted APIs or paid services, web UI, database, user accounts, ML
training.

## V5 Scope
Input: a video URL or local file, or a list of them; and the developer's
judgments on drafts, made in a local review page. Output: the same folder of
drafts, with candidates of 15 to 90 s that end where the moment ends
([ADR 0023](decisions/0023-candidate-boundaries-follow-the-moment.md)),
titles that know the channel and VOD title, and picking changes measured
against the developer's own verdicts
([ADR 0012](decisions/0012-evaluate-detectors-against-hand-labels.md), V5
amendment). Interface: `clipper review`
([ADR 0024](decisions/0024-review-drafts-in-a-local-browser-page.md)), a
list of sources for `drafts`, and judged-label reporting in `eval`. Tuned on
streamer VODs first.

The review page reverses the web UI non-goal for one local, single-user
page. Non-goals for V5: posting or uploads, analytics or platform metrics,
hosted APIs or paid services, the movies/TV niche, tuning for other niches,
channel watching or scheduling, audience signals as a detector input, a
trained model, a database, user accounts.

## Success Criteria
1. `clipper analyze talk.mp4` runs end to end on a local machine and outputs
   at most 5 candidates. Measured on a 5:14 speech video on CPU with the
   Whisper `base` model: 14.3 s cold, 0.4 s warm. A 5:04:01 stream on the
   same setup (CPU, `base`, int8, model already downloaded): 20:59 cold
   (1,260 s), 0.67 s warm. Each video was timed once. On the RTX 4070 Ti the
   stream took 593 s, but its transcript drifted from the CPU's, so
   transcription stays on the CPU
   ([ADR 0021](decisions/0021-gpu-transcription-measured-not-adopted.md)).
2. Heuristic detection is deterministic: the same transcript always produces
   the same top 5. An LLM detector is aimed at being repeatable on one setup
   and is not guaranteed to be
   ([ADR 0015](decisions/0015-determinism-is-per-detector.md)). Transcription
   itself may vary slightly between runs and model versions. Results are saved
   to disk.
3. A new transcriber or detector can be added by writing one new class,
   with no changes to the pipeline or domain code.
4. Core logic has automated tests that run without a real video or
   network access.
5. V4: one command takes a URL to a folder of vertical, captioned, titled
   drafts on the developer's machine, with nothing paid. Its time on a long
   stream is measured and recorded, not assumed. Measured: `clipper drafts`
   on the 5:04:01 stream from a local file, cold, took 22:58 (1,378 s) for 5
   drafts; a short video from its URL took 71.5 s
   ([ADR 0022](decisions/0022-one-command-to-drafts.md)). Timed once each.

## Principles
1. Each stage is independent and replaceable.
2. Every stage has typed inputs and outputs.
3. Everything runs locally and uses free and open-source tools.
4. Build the simplest thing, and put seams where we know change is
   coming.
5. Usefulness first. When daily usefulness and ML depth conflict, usefulness
   wins. ML changes are measured with `eval` and grow once the tool is in use.

## Known Risks
- Heuristic scoring may not match what audiences actually find engaging.
  V1 is a baseline to measure future approaches against.
- V3 labels are one person's judgment on a few videos. They can show that a
  detector change is a sanity-check improvement, not that audiences agree
  ([ADR 0012](decisions/0012-evaluate-detectors-against-hand-labels.md)).
- The two label files so far were test examples, not a sample of the videos
  the developer will process, so `eval` results say little about real work
  yet.
- About half of the moments the developer would clip are visual, and every
  detector so far reads only the transcript.
- YouTube's and Twitch's terms forbid downloading with a tool, as read on
  2026-11-03. `clipper fetch` downloads one video at a time for personal use,
  with that risk accepted and stated
  ([ADR 0020](decisions/0020-fetch-a-video-by-url.md)).
- Fitting a 16:9 frame into 9:16 makes the picture small: about a third of
  the vertical frame. Following the subject instead needs tracking, a V4
  non-goal.
- CPU transcription of long videos may be slow. Transcripts are cached
  to mitigate this.
- Re-encoding uses libx264, which is GPL. Clipper does not ship ffmpeg, and the
  project's own license is still to be decided.
