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

## Amended by the thinking fix (V3 item 7c)

The first real run (manual step L1, Ollama 0.40.13, `qwen3.5:9b`, RTX 4070 Ti)
failed on window 15 of 18 of `test_input.json`, 0:03:32-0:04:14:
`Model reply for window 0:03:32-0:04:14 is not a valid score: <root>: Invalid
JSON: EOF while parsing a value at line 1 column 0 (got '')`.

- **Cause.** `qwen3.5` is a thinking model (its `/api/show` capabilities
  include `thinking`), and Ollama lets it think when the request does not say
  otherwise. Sent on its own, that window's request had a 293-token prompt
  and generated 3,803 tokens, about 15,000 characters of thinking, then
  stopped with `done_reason: length` and an empty `response`. The context was
  4,096 tokens. The 14 windows before it took 18.75 s to 1 m 10 s each, the
  first including the model load.
- **Decision.** `OllamaClient` sends `think: false` with every request. A score
  per window is a short judgement, not a reasoning task, and at those times the
  1,215 windows of `oFbD0Ot7nQ8.json` would take hours. With `think: false`
  the same window took 0.8 s and generated 48 tokens, a valid `WindowScore`.
- **Models without thinking.** `llama3.1:8b` (capabilities `completion`,
  `tools`) accepts `think: false` and gave the same reply as without it, so
  the flag is sent unconditionally and the client does not ask `/api/show`
  first.
- **Tradeoff.** Whatever thinking might add to a score is given up. If it is
  wanted later, it is a setting and a further amendment, measured with `eval`.
- **Not changed here.** The client does not read `done_reason`, so a reply cut
  off at the length limit still surfaces as a validation error on an empty or
  partial reply.

## Amended by the model choice (V3 item 7c)

Manual steps L1 to L5 were run on 2026-11-02 with thinking off: Windows 11,
Ollama 0.40.13, RTX 4070 Ti 12 GB, `qwen3.5:9b` and `llama3.1:8b`, both
`100% GPU` in `ollama ps`. Numbers below are from those runs.

| Step | Heuristic | `qwen3.5:9b` | `llama3.1:8b` |
|---|---|---|---|
| L3: two `analyze` runs, saved JSON | (deterministic, 0015) | identical | same candidates and scores; 2 of 6 reasons worded differently |
| L4: `test_input.json`, top 5 | recall 3/4, precision 3/5, 2 repeats | 4/4, 4/5, 1 repeat, 19.2 s | 4/4, 4/5, 1 repeat, 13.7 s |
| L5: `oFbD0Ot7nQ8.json`, top 30 | 5/30, 5/30, 2 repeats | 6/30, 6/30, 953.4 s (0.78 s per window) | 3/30, 3/30, 3 repeats, 685.2 s (0.56 s per window) |
| L5: top 5 (first five rows) | 2/30, 2/5 | 0/30, 0/5 | 0/30, 0/5 |

- **Ties (the 7a question).** Scores cluster on round values. On
  `oFbD0Ot7nQ8.json`, `qwen3.5:9b` used 11 distinct scores over 1,215 windows
  (0.9 for 6, 0.85 for 28, 0.2 for 646); `llama3.1:8b` used 7 (0.8 for 403,
  0.6 for 421). In L5, `llama3.1:8b`'s rows 3 to 30 all scored 0.80, so the
  earlier-start tie rule filled its top 30 from the first 34 minutes.
- **Heuristic score as the tie-break, measured and not adopted.** A scratch
  script (not committed) scored every window once per model and re-ranked by
  `(-llm, -heuristic, start)`, with `select_top`'s overlap rule and
  `evaluate`. Its earlier-start rows reproduced L4 and L5. For `qwen3.5:9b`
  it changed nothing on `oFbD0Ot7nQ8.json` (top 5: 0/30; top 30: 6/30) and
  lost a label on `test_input.json` (recall 3/4). For `llama3.1:8b` it raised
  top 30 to 9/30 and top 5 to 1/30, but with 403 windows tied at the cut that
  is the heuristic's ranking inside one LLM score band, not the LLM's. The tie
  rule stays as it is. Ties are not what keeps the top five off the labels;
  both models' highest-scored windows are not the labelled ones.
- **Decision.** `CLIPPER_LLM_MODEL` defaults to `qwen3.5:9b`: repeatable in L3,
  the widest spread of scores, and level with or slightly above the heuristic
  at top 30 on the long video. `heuristic` stays the default detector, because
  neither model beats it at top 5 there. An empty `CLIPPER_LLM_MODEL` is still
  an error (0013).
- **Score scale.** Decision 4 stands for now. A finer score (token
  probabilities, or a rubric in the prompt) is the next lever, and needs its
  own amendment with `eval` results.
- **Repeatability (0015).** `llama3.1:8b`'s reasons varied between two runs at
  temperature 0 and seed 0, though its scores did not. That meets 0015's
  "revisit when" for that model; it is not the default.

## Amended by the `drafts` defaults (V5 item 11)

`drafts` uses the LLM detector and drafts the top 10 by default. `analyze`
and `eval` keep `heuristic` and `CLIPPER_TOP_N` (default 5). This replaces
7c's "heuristic stays the default detector" for `drafts` only.

**Evidence.** The V5 judged labels (0012) on the three judged streamer
VODs at top 10, heuristic with ADR 0023 items 5 and 6, LLM (`qwen3.5:9b`)
from the baseline drafts. K is a keeper (on a `post` or `fix` label), r a
draft on a rejected span, `.` unjudged:

| VOD | Heuristic, ranks 1 to 10 | LLM, ranks 1 to 10 |
|---|---|---|
| v2922695332 (jasontheween) | `rrrKrrrrKK` (3) | `KKrKrrKKrK` (6) |
| v2917872953 (stableronaldo) | `KKKKKKrKrK` (8) | `KKrKrKrKKK` (7) |
| v2922589775 (adapt) | `rKrrrrr.rr` (1) | `KrKrrKKrrK` (5) |
| Total | 12: 6 in ranks 1 to 5, 6 in 6 to 10 | 18: 8 in ranks 1 to 5, 10 in 6 to 10 |

- **Detector.** The LLM has more keepers on two VODs and one fewer on the
  third. Two LLM-only runs agree: 9 of 10 kept on a podcast (5pk8HLcAAT0)
  and 10 of 10 on a Lacy video (XwH_o5xboKA), with no heuristic run to
  compare.
- **Top N.** Ranks 6 to 10 hold as many keepers as ranks 1 to 5 or more,
  for both detectors, as the maintainer asked in the V5 interview. Ranks 11
  and up are unjudged, so nothing supports more than 10, and each draft
  costs about a minute to judge.
- **Ties.** All of the LLM's top 10 score 0.95 or 0.9, so inside a band the
  order is earliest start (decision 4). Ties do not decide the detector, so
  the finer score stays parked.

**Decision.**

- `drafts --detector` defaults to `llm`; `--detector heuristic` stays.
- `drafts --top N`, default 10, at least 1, sets the detector's top N for
  that run. `CLIPPER_TOP_N` does not apply to `drafts`; its default stays 5,
  so V1's `analyze` output does not change.
- With the LLM detector, an empty `CLIPPER_LLM_MODEL` fails before any
  download, with titles off too (0022). The `heuristic` needs no model.

**Tradeoffs.**

- About 15 to 17 minutes per VOD against 2 to 3, with cached transcripts.
  Overnight runs make that acceptable (V5 interview).
- The LLM's keepers end early more often (6 against 1 at top 10); end
  extension did not help it (0023 item 5), so those ends are moved in the
  review page.
- Ollama must be running by default, as titles already need. A stopped
  Ollama fails a source after its download and transcription, as titles
  do today; checking it up front is not part of this change.
- `drafts` and `analyze` pick different moments by default.

**Revisit when** the heuristic gains a signal the LLM lacks (loudness, V5
item 10), a finer LLM score lands, or a larger Whisper model (item 12)
changes either detector's keepers.

## Amended by detector examples (V6 item 4, ADR 0026): measured, not adopted

**Tried.** The system prompt gained 4 kept and 4 rejected excerpts (each
the first 60 words of a judged span's transcript) from other videos'
judged labels, drawn at random (seed 0, the podcast left out), labelled
"kept, worth posting" and "rejected, not worth posting", with a line to
judge each new excerpt the way the editor did but on its own words. The
same examples served every window of a video, so the prompt prefix was
shared; time per VOD was unchanged (235 to 618 s against 236 to 584 s).

**Measured** at top 10 on four short streamer VODs with every draft
judged (3Sbj_BZORr4, v2918956109, v2922292514, GBCYViJBuCI), each with
examples from the other videos only (ADR 0026 decision 3). The examples
prompt picked 22 windows never judged; the maintainer judged all of them
in `review` (scratch folders, labels written to `data/labels/picks/`).
Keepers / rejected / repeats:

| VOD | No examples (as now) | With examples |
|---|---|---|
| 3Sbj_BZORr4 | 7 / 3 / 0 | 7 / 3 / 0 |
| v2918956109 | 9 / 1 / 0 | 6 (8) / 1 / 1 |
| v2922292514 | 5 / 5 / 0 | 5 / 5 / 0 |
| GBCYViJBuCI | 8 / 2 / 0 | 7 / 3 / 0 |
| Total | 29 / 11 / 0 | 25 (27) / 12 / 1 |

- The examples reshuffled the top 10 rather than sharpening it: 14 of the
  29 known keepers and 8 of the 10 known rejects dropped out, and of the
  22 new windows the maintainer kept 10 and rejected 10.
- The two in brackets on v2918956109 are new windows the maintainer would
  save, but `review` refused them: each overlaps a keeper already saved
  (by 11 and 13 s) without being a hit under ADR 0012's rule. Counted as
  keepers, the examples are still 2 keepers behind.

**Decision.** Not adopted; no code merged. The 20 judged spans stay in the
labels. Fixed examples do not help this model; examples chosen per window
by similarity (ADR 0026 decision 5) are a separate idea, after item 5's
embeddings.

## Amended by the shared client move (V7 item 4)

Behaviour-neutral. The title writer (0018) used the detector's client, so
`adapters/titles/` imported from `adapters/detection/`, and the client
raised `DetectionError`, which titles re-raised as `TitleError`.

- `OllamaClient` and the `TextGenerator` Protocol moved to
  `adapters/llm/ollama.py`; the "Two classes" decision above stands with
  that path.
- The client raises `LlmError` (`domain/errors.py`). `LlmClipDetector`
  turns it into `DetectionError` and the title writer into `TitleError`,
  each with the message unchanged, so every command prints what it did
  before. Requests, timeouts and the payload are unchanged; no manual
  step.
- The Parked `done_reason` note (a reply cut off at the length limit
  reads as `not a valid score ... (got '')`) is not part of this move.

## Related

- 0012: evaluation
- 0013: detector choice
- 0015: determinism
