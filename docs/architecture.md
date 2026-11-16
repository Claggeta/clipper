# Architecture

This is the V1 design: a local video in, the top 5 clip-worthy moments out.
Later versions (rendering, `fetch`, `drafts`, `review`, the LLM detector) are
recorded one decision at a time in [`decisions/`](decisions/), and the
current list is in `CLAUDE.md`.

```text
Video file
   │
   ▼
[AudioExtractor] ──► audio.wav
   │
   ▼
[Transcriber]    ──► Transcript (segments with start/end/text)
   │
   ▼
[ClipDetector]   ──► list[ClipCandidate] (top 5, ranked)
   │
   ▼
[CLI]            ──► table + JSON
```

## Layers

- **Domain** holds pure data models, with no I/O and no third-party imports
  beyond validation. It is the most stable layer.
- **Core** defines the interfaces (`AudioExtractor`, `Transcriber`,
  `ClipDetector`) as Python Protocols, plus the pipeline that composes them
  and the settings.
- **Adapters** are concrete implementations: `FfmpegAudioExtractor`,
  `FasterWhisperTranscriber`, `HeuristicClipDetector`, a caching
  transcriber and a fake for tests.
- **CLI** is the composition root. It parses arguments, picks the concrete
  adapters, builds the pipeline and prints.

The dependency rule is that arrows point inward. Adapters depend on core and
domain, core on domain, and domain on nothing. The CLI is the one place that
imports adapters, to choose them; nothing imports the CLI.

## Key decisions

- **Transcription:** faster-whisper (open source, local). Alternatives: the
  OpenAI Whisper package (slower, heavier), whisper.cpp (fast, but awkward
  from Python), or cloud APIs (cost, privacy, not free). Tradeoff:
  faster-whisper gives word-level timestamps and runs on CPU, but the models
  are large downloads and CPU runs are slow. It starts with the `base`
  model.
- **Audio extraction:** ffmpeg through subprocess. It's the industry standard
  and free. The cost is an external system dependency, so it is documented,
  and its presence on PATH is checked when it is first run, with a clear
  error if it is missing ([ADR 0004](decisions/0004-report-ffmpeg-errors.md)).
- **Clip detection V1:** transparent heuristics, not ML. Signals:
  - speech pace and density
  - questions and exclamations
  - emotional or strong keywords
  - pauses that bound natural segments
  - sentence-boundary alignment, so clips don't start mid-word

  Windows of roughly 20–60 seconds are scored and ranked. Overlapping
  windows are deduplicated so the results aren't five near-identical
  windows.
- **Why not an LLM in V1?** There was no labelled data to judge "good", and
  an LLM would hide that gap. Heuristics give a baseline any later approach
  must beat. (V3 added the LLM detector and measured it against labels,
  [ADR 0014](decisions/0014-local-llm-detector-via-ollama.md).)
- **Extensibility:** `ClipDetector` is an interface, so a local LLM or a
  trained model is a second implementation. A `ClipCandidate` carries a
  score and reasons, which makes it explainable and usable later as
  training data.
- **Data models:** pydantic v2, over standard dataclasses. Dataclasses add
  no dependency, but pydantic gives validation and JSON serialization, and
  these models are serialized constantly.
- **Persistence:** JSON files, no database. Because the pipeline returns
  domain objects, a repository later is a contained change. The transcript
  is cached on disk, since transcription is slow and detection is re-run
  often while tuning it.
- **Configuration:** a typed settings object (pydantic-settings) with
  defaults and `CLIPPER_*` environment variable overrides. Commands take
  their own options (`--detector`, `--top`, `-v`), not a flag per setting.
  No magic constants buried in code.
- **Errors:** a small exception hierarchy (`ClipperError` →
  `AudioExtractionError`, `TranscriptionError`, ...) so the CLI shows
  friendly messages.
- **Logging:** the standard `logging` module, not `print` (except for the
  final output).
