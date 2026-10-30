# Clipper Vision

## Problem
Watching hours of long-form video and manually cutting clips is too much
of a time sink.

## Vision
A modular platform that generates ready-to-publish clips from long-form
content. Over time it learns from performance data and generates better
clips.

## Users
V1 user: the developer, running locally. No other users are assumed.

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

## Success Criteria
1. `clipper analyze talk.mp4` runs end to end on a local machine and outputs
   at most 5 candidates. Measured on a 5:14 speech video on CPU with the
   Whisper `base` model: 14.3 s cold, 0.4 s warm. A 30-minute video is not
   yet measured, and will scale roughly with audio length.
2. Detection is deterministic: the same transcript always produces the
   same top 5. Transcription itself may vary slightly between runs and
   model versions. Results are saved to disk.
3. A new transcriber or detector can be added by writing one new class,
   with no changes to the pipeline or domain code.
4. Core logic has automated tests that run without a real video or
   network access.

## Principles
1. Each stage is independent and replaceable.
2. Every stage has typed inputs and outputs.
3. Everything runs locally and uses free and open-source tools.
4. Build the simplest thing, and put seams where we know change is
   coming.

## Known Risks
- Heuristic scoring may not match what audiences actually find engaging.
  V1 is a baseline to measure future approaches against.
- CPU transcription of long videos may be slow. Transcripts are cached
  to mitigate this.
