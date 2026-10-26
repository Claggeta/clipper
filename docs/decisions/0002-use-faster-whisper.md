# 0002: Use faster-whisper for transcription

- Status: Accepted
- Date: 2026-10-26

## Context
Version 1 must turn the audio of a long-form video into a transcript with
start and end timestamps. Timestamps are required because clip detection
works on timed segments. The tool must be free, open source, and run
locally. Hosting costs, privacy, and API quotas are all things we want to
avoid. Transcription sits behind a `Transcriber` interface, so this
decision can be reversed later.

## Decision
Use faster-whisper, a CTranslate2-based reimplementation of OpenAI's
Whisper models, as the first `Transcriber` adapter. Start with the `base`
or `small` model.

## Alternatives considered
- **OpenAI `whisper` package:** the reference implementation. It is
  slower and uses more memory than faster-whisper for the same models.
- **whisper.cpp:** very fast and lightweight, but it is a C++ project.
  Using it from Python means bindings or subprocess calls, which adds
  integration work.
- **Cloud transcription APIs:** accurate and easy to call, but they cost
  money, send user content to a third party, and break the free and local
  requirement.

## Consequences
- (+) Free, local, and runs on CPU without a GPU.
- (+) Provides segment and word-level timestamps.
- (+) Runs faster than the original Whisper package.
- (-) Model files are large downloads (hundreds of MB to several GB).
- (-) CPU transcription of long videos can be slow, so we cache
  transcripts on disk.
- (-) Accuracy depends on model size, so smaller models trade quality
  for speed.
- (-) Adds a dependency tied to a specific runtime (CTranslate2).
