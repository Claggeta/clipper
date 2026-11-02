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

## Amended by the extraction PR (V3 item 6)

- Decision 6 is done. Windowing and overlap deduplication live in
  `adapters/detection/windows.py`, next to `signals.py`, not in `core/`: only
  detectors use them, and the window and step are detection choices.
- `sliding_windows(transcript, window_seconds, step_seconds)` returns the
  whole-segment windows, and `select_top(candidates, top_n)` ranks by score
  (ties by earlier start) and skips any candidate that overlaps one already
  chosen. Touching candidates do not overlap.
- `WINDOW_SECONDS = 45.0` and `STEP_SECONDS = 15.0` are module constants and
  the heuristic's defaults, so the LLM detector scores the same windows from
  one definition.
- `min_score` stays a `HeuristicClipDetector` parameter, applied before
  `select_top`.
- Behaviour kept as it was: windows step from the first segment's start, and a
  segment longer than the window is in no window. Changing either is a
  detector change, not part of the extraction.
- Checked as behaviour-neutral: the heuristic's tests are unchanged, and on
  the two saved analyses (`test_input.json`, `oFbD0Ot7nQ8.json`) the
  heuristic's candidates at top 5, 30 and 1000, and `eval`'s output at top 5
  and 30, were byte-identical before and after.

## Amended by the LLM detector PR (V3 item 7a)

V3 item 7 is three PRs: the adapter (this one, not reachable from the CLI),
`--detector llm` (7b), and the manual steps, first real runs and model choice
(7c). Nothing in this PR has run against a real Ollama server or model.

- **HTTP client.** Python's standard library (`urllib.request`, `json`). No
  dependency is added, so the check against 0003 does not arise. `requests` is
  in the lock only through the pinned `huggingface-hub`, and `httpx` and the
  `ollama` package would be new dependencies, `httpx` from the family Hub 2.x
  churned.
- **Two classes.** `OllamaClient` (`adapters/detection/ollama.py`) is the
  transport. It POSTs one request per prompt to `<CLIPPER_LLM_URL>/api/generate`
  with `stream: false`, `format` set to a JSON schema, and
  `options: {temperature: 0, seed: 0}`, and returns the reply text. The timeout
  is 120 s per request, because the first request also loads the model.
  `LlmClipDetector` (`adapters/detection/llm.py`) owns the prompt, validation
  and ranking, and takes any object with the same `generate` method (a
  Protocol in that module), so its tests need no server.
- **Every window is scored**, one request per window from `sliding_windows`,
  in order, and not only the windows where a heuristic signal fires. The point
  is to judge meaning the heuristic misses. On the two saved analyses that is
  18 requests (`test_input.json`, 274 s) and 1,215 (`oFbD0Ot7nQ8.json`,
  18,234 s). Filtering by the heuristic first stays the "too slow" lever under
  "Revisit when".
- **Score scale.** Decision 4 stands: the model is asked for a score from 0
  to 1. At temperature 0 a model may favour round values, and `select_top`
  breaks ties by the earlier start, which would favour the start of a video.
  7c records how many windows tie at the top-N cut. If ties decide the result,
  the scale changes in a further amendment.
- **Validation.** The reply must parse as `WindowScore`: `score` from 0 to 1,
  and a non-empty `reason` after stripping whitespace. Its JSON schema is the
  `format` sent to Ollama. The reason becomes the candidate's only `reasons`
  entry.
- **Errors.** `DetectionError`, a new `ClipperError`, for: the server cannot be
  reached; an HTTP error, carrying Ollama's own `error` text rather than
  matching it, plus a pull hint on 404; a timeout; a response body without a
  string `response`; and a reply that fails validation, naming the window and
  quoting the start of the reply. There is no retry. At temperature 0 with a
  fixed seed a retry would repeat the reply, so one bad window ends the run.
- **Settings.** `CLIPPER_LLM_URL` defaults to `http://127.0.0.1:11434`,
  Ollama's documented default address, not yet tried here.
  `CLIPPER_LLM_MODEL` has no default. 7b makes an unset model a clear error,
  and 7c sets the default to the model chosen from `eval` results.
- **Progress** is logged at INFO every 50 windows and after the last one.
- **The prompt** is a module constant, not recorded in results. It changes
  only through PRs, so git history records it. 7c may revise it while
  choosing a model.

## Related

- 0012: evaluation
- 0013: detector choice
- 0015: determinism
