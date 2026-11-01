# 0014: Local LLM detector that re-ranks the heuristic's windows, via Ollama

- Status: Accepted
- Date: 2026-11-01

## Context

The heuristic uses question marks, exclamation marks, a fixed word list and
pace. On Whisper `base` output the first two rarely fire (README, Known
limitations). An LLM can judge meaning, such as a hook or a self-contained
point, which the heuristic cannot. Everything must stay local and free
(vision principle 3). The maintainer's machine has an RTX 4070 Ti with 12 GB, a
13700K and 32 GB of RAM. That is plausible for a quantized model of roughly
7 to 14 billion parameters. It has not been tried here.

## Decision

1. A new adapter implements the existing `ClipDetector` Protocol. Nothing in
   `core/` or `domain/` changes for it (vision success criterion 3).
2. **It re-ranks; it does not propose boundaries.** The windows are the
   heuristic's (45 s, stepped by 15 s, whole segments only). The LLM scores each
   window, and the existing overlap deduplication and top-N selection pick the
   result. Candidate bounds therefore stay comparable with the heuristic's, and
   `eval` (0012) compares like with like.
3. The model runs behind **Ollama**, a local server the adapter calls over HTTP
   on the loopback address. Model name and server URL are settings
   (`CLIPPER_LLM_MODEL`, `CLIPPER_LLM_URL`). No model is chosen in this ADR.
   It is chosen in the adapter's PR by running `eval`, and the choice is
   recorded there.
4. The LLM returns a score from 0 to 1 and a one-line reason per window as
   structured JSON, validated by a pydantic model. Temperature 0 and a fixed
   seed are requested. The reason becomes the candidate's `reasons`, so the
   output stays explainable.
5. Failures are errors, not fallbacks. Server not running, model not pulled and
   output that fails validation each raise a `ClipperError` with a message that
   names the cause. The adapter never quietly returns the heuristic's answer.
6. Windowing and overlap deduplication currently live inside
   `HeuristicClipDetector` as private code (inferred from reading it). They are
   extracted into shared code first, as a behaviour-neutral change in its own
   PR with the heuristic's existing tests unchanged, before the LLM adapter is
   written.
7. A manual-test step is required: CI cannot run a model. It checks that the
   server is reachable, that a run on a real transcript produces valid output,
   and records the time taken.

## Alternatives considered

- **llama-cpp-python in process.** No service to run, but a harder install on
  Windows and a heavier dependency for the project. Not tried.
- **The LLM proposes its own boundaries from the whole transcript.** Closer to
  the end goal, but it changes the problem, and `eval` could no longer say the
  difference comes from scoring. Deferred.
- **A hosted API.** Rejected: it costs money and sends content out
  (principle 3).
- **Scoring every window with an embedding model and no LLM.** Not rejected,
  not explored. It remains an option if the LLM is too slow.

## Consequences

- (+) One new class and settings. `analyze` and `eval` pick it with
  `--detector llm` (0013).
- (+) The heuristic stays as the baseline every LLM result is measured against.
- (-) A service the user must start, and a model download.
- (-) Speed is unmeasured. A 5:14 video has about 20 windows. A two-hour stream
  has several hundred, each a model call. Cost grows with video length, and
  nothing here says it is acceptable.
- (-) Adding an HTTP client dependency must be checked against 0003 before it is
  added.
- (-) Determinism is weaker than for the heuristic (0015).

## Revisit when

- Ollama does not run well on this machine. Try the in-process option.
- Scoring a long video takes too long to be usable. Shorten the prompt, filter
  windows with the heuristic first, or change the scoring method.
- `eval` shows re-ranking caps what the LLM can do, because good moments are not
  among the heuristic's windows. Then try the boundary-proposing approach.

## Related

- 0012: evaluation
- 0013: detector choice
- 0015: determinism
