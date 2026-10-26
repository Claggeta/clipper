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
[Presenter/CLI]  ──► table + JSON

Layers:

Domain holds pure data models, with no I/O and no third-party imports beyond validation. It is the most stable layer.
Core services define the interfaces (Transcriber, ClipDetector) as Python Protocols, plus the pipeline that composes them.
Adapters are concrete implementations: FasterWhisperTranscriber, HeuristicClipDetector, and an ffmpeg audio extractor.
Interface layer is the CLI. It parses args, builds the pipeline, and prints.
The dependency rule is that arrows point inward. The CLI depends on core, adapters depend on core and domain, and domain depends on nothing.

Key decisions:

Transcription: faster-whisper (open source, local).
Alternatives: the OpenAI Whisper package (slower, heavier), whisper.cpp (fast, but awkward from Python), or cloud APIs (cost, privacy, not free).
Tradeoff: faster-whisper gives word-level timestamps and runs on CPU, but the models are large downloads and CPU runs are slow. Start with the small or base model.
Audio extraction: ffmpeg through subprocess. It's the industry standard and free. The cost is that it's an external system dependency, so we'll document it and check for it at startup.
Clip detection V1: transparent heuristics, not ML. Candidate signals:
speech pace and density
questions and exclamations
emotional or strong keywords
pauses that bound natural segments
sentence-boundary alignment so clips don't start mid-word
Windows of roughly 20–60 seconds are scored and ranked. Overlapping windows are deduplicated so you don't get five near-identical results.
Why not an LLM now? We have no labeled data to judge "good", and an LLM would hide that gap. Heuristics give a baseline that any future ML approach must beat. This is how real ML projects start.
Extensibility: ClipDetector is an interface. A local LLM or a trained model becomes a second implementation. The ClipCandidate carries a score and reasons, which makes it explainable and later usable as training data.
Data models: pydantic v2 or standard dataclasses.
Tradeoff: dataclasses add no dependency, while pydantic gives validation and easy JSON serialization. I recommend pydantic, because you'll serialize these constantly and validation catches bad data at the boundaries.
Persistence: JSON files, no database yet. A DB now would be speculative. Because the pipeline returns domain objects, adding a repository later is a contained change. Cache the transcript on disk, though. Transcription is slow, and you'll re-run detection constantly while tuning it.
Configuration: a typed settings object (pydantic-settings) with defaults, env var overrides, and CLI flags. No magic constants buried in code.
Errors: a small custom exception hierarchy (ClipperError → AudioExtractionError, TranscriptionError) so the CLI shows friendly messages.
Logging: the standard logging module, not print (except for the final result output).