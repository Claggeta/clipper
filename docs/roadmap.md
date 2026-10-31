| Milestone           | Outcome                                                     | Learning focus                                | Status      |
|---------------------|-------------------------------------------------------------|-----------------------------------------------|-------------|
| M0: Foundation      | Repo, tooling, CI, empty package, one passing test          | Packaging, linting, git workflow              | Done        |
| M1: Domain models   | Typed data structures (Transcript, Segment, ClipCandidate)  | Domain modeling, validation                   | Done        |
| M2: Transcription   | Video → audio → transcript behind an interface              | Interfaces, external dependencies, adapters   | Done        |
| M3: Clip detection  | Transcript → top 5 candidates via heuristics                | Strategy pattern, testing pure logic          | Done        |
| M4: Pipeline + CLI  | End-to-end command with display and JSON output             | Orchestration, config, logging                | Done        |
| M5: Hardening       | Error handling, docs, test coverage, v0.1.0 release         | Quality, semantic versioning                  | Done        |
| V2                  | Clip rendering with ffmpeg, captions (v0.2.0)               | Subprocess handling, media                    | Done        |
| V3 (later)          | Local LLM scoring, plus evaluation against labeled examples | ML evaluation                                 | To do       |
| V4+ (later)         | Uploads, analytics, learning loop, database, API            | Persistence, integrations                     | To do       |