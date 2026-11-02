# 0018: Suggested title and hook per clip, via Ollama

- Status: Accepted
- Date: 2026-11-02

## Context

The maintainer posts by hand and needs a title and a hook line for each clip
before daily use (V4 scope). The LLM detector (0014) already talks to a local
Ollama server, with the standard library, structured JSON output, temperature
0 and `think: false`. Everything must stay local and free (vision principle 3).

## Decision

1. A `TitleWriter` Protocol in `core/` takes a clip's transcript text and
   returns a title and a hook. A pydantic model holds them; both must be
   non-empty after stripping whitespace.
2. The Ollama adapter reuses `OllamaClient` (0014): one request per clip, a
   JSON schema `format`, temperature 0, seed 0, `think: false`. The model is
   `CLIPPER_LLM_MODEL`, shared with the detector. The prompt is a module
   constant that changes only through PRs.
3. `render --titles` turns it on. Titles are text in the folder's manifest
   (0019), not drawn on the video.
4. A title failure is that clip's failure under 0011: the clip is listed, the
   rest still render, and the command exits 1. There is no fallback, as in
   0014.
5. The CLI builds the writer through the context object that replaces the
   private factories first (0013's trigger: this would be the fifth factory).

## Alternatives considered

- **A separate `clipper titles` command.** Keeps `render` free of Ollama, but
  adds a command and a second pass over the clips. Not chosen.
- **Burn the hook into the video.** More styling work in V4. Later, if wanted.
- **Titles at analysis time, in `AnalysisResult`.** A domain change, and it
  would title candidates that are never rendered. Rejected.
- **A hosted model.** Rejected (principle 3).

## Consequences

- (+) No new dependency or service beyond the one `--detector llm` uses.
- (+) Titles sit next to the clips they describe.
- (-) `render --titles` needs Ollama running with the model pulled.
- (-) Title quality is unmeasured; no labels exist for it. It is judged by eye.
- (-) Repeatability is per model (0015).
- (-) One model setting serves two jobs. If they need different models, that is
  a new setting and an amendment.

## Revisit when

- Titles need a different model from the detector.
- The maintainer wants the hook on screen.

## Related

- 0013: Choose the detector with a CLI option (the factory trigger)
- 0014: Local LLM detector via Ollama
- 0015: Determinism is per detector
- 0019: A folder per video
