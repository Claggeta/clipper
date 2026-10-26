# 0001: Use pydantic for domain models

- Status: Accepted
- Date: 2026-10-26

## Context
Domain objects (Transcript, Segment, ClipCandidate) will be serialized
to JSON for caching and output, and loaded back from disk. Cached files
may be corrupt or outdated.

## Decision
Use pydantic v2 for domain models.

## Alternatives considered
- Standard dataclasses: no dependency, but no runtime validation and
  manual JSON handling.

## Consequences
- (+) Validation at I/O boundaries, built-in JSON serialization.
- (+) Shares a foundation with pydantic-settings for configuration.
- (-) One more dependency to keep updated.
- (-) Slightly more overhead than plain dataclasses.
