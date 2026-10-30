# 0003: Pin transcription dependencies

- Status: Accepted
- Date: 2026-10-30

## Context
The first real run of `clipper analyze` on a video failed during
transcription with:

    open() got an unexpected keyword argument 'metadata_errors'

The transcript pipeline could not run at all. CI was green throughout,
because import-level checks and the fake transcriber never exercise the
real decoding path.

Two independent problems were found in the dependency graph:

1. faster-whisper 1.2.1 calls `av.open(..., metadata_errors="ignore")`.
   The installed av 19.0.2 does not accept that argument, so every call
   to `decode_audio` raised TypeError.
2. huggingface-hub 2.3.0 changed its API and its dependency graph,
   pulling in httpx2, httpcore2 and five other packages in place of
   httpx. faster-whisper 1.2.1 was built against the 0.x line.

Only the first problem actually broke the run. The second was found while
diagnosing it and is a hazard independent of the failure.

## Decision
Pin both packages:

    huggingface-hub<1.0
    av==17.0.0

## Alternatives considered
- **Float both and rely on the resolver:** rejected. It selected the
  broken combination once already, and nothing in the project can
  detect that class of failure before a real run.
- **Pin only av:** rejected. Fixes the crash but leaves the Hub free to
  resolve back to 2.x, which changes a dozen transitive packages.
- **Pin every transitive dependency:** rejected. Trades one understood
  pin for a large, unexplained set that nobody will maintain.
- **Vendor a local model and drop the Hub at runtime:** viable, and
  worth revisiting if the Hub causes further trouble. Not needed now.

## Consequences
- (+) Transcription works. Verified on a 5:14 speech video with the
  base model: 5 candidates produced, deterministic across runs.
- (+) The Hub's 2.x dependency-graph change is avoided.
- (+) The working combination is recorded in uv.lock, so CI and local
  runs resolve identically.
- (-) Two packages are pinned below their latest releases and will age.
  Security fixes or features available only above these versions will be
  unavailable until the pins are lifted.
- (-) The pins compensate for constraints faster-whisper does not declare
  itself, so they may look unnecessary to a future reader. This ADR is
  the answer to that.

## Revisit when
- A faster-whisper release declares huggingface-hub>=1.0 in its own
  project metadata.
- The av `metadata_errors` incompatibility is fixed upstream, allowing av
  to float again.

## Related
- 0002: Use faster-whisper for transcription
