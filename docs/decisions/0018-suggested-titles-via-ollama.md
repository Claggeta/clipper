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

## Amended by the `render --titles` PR (V4 item 5)

- **Protocol.** `TitleWriter.write(text) -> ClipTitle` in
  `core/interfaces.py`. `ClipTitle` is in `domain/models.py`; `TitleError`,
  a new `ClipperError`, is its failure.
- **What the model sees: the clip's spoken text only.** `clip_text(transcript,
  candidate)` in `core/captions.py` selects what `build_ass` shows: words that
  start inside the span, and the whole text of a segment without words (a
  v0.3 analysis) that overlaps it, with the same markup characters removed.
  The candidate's reasons, score and the video's file name are not sent:
  reasons would steer the title toward the detector's wording, and file names
  are video IDs.
- **No speech in range.** The clip renders without a title, `render` prints
  `Note: #N <span> has no speech in range; no title`, and its manifest entry
  has `title` and `hook` `null`. It is not a failure: no text is made up, so
  this is not the fallback decision 4 rules out. It matches captions (0011,
  item 10 amendment).
- **Valid.** Decision 1, plus one line: a `\n` or `\r` in either field fails
  validation. A newline is rejected, not joined, since joining would be a
  silent fix. **Length is asked for in the prompt** (about 60 characters for
  the title, 100 for the hook) **and not checked**: a cap would fail a usable
  clip over a 65-character title. If runs give runaway text, a cap is a
  further amendment.
- **Adapter.** `OllamaTitleWriter` in `adapters/titles/ollama.py` takes the
  `TextGenerator` Protocol from `adapters/detection/llm.py`, and the CLI gives
  it an `OllamaClient`, unchanged: temperature 0, seed 0, `think: false`, the
  120 s timeout. The `format` schema is `ClipTitle`'s JSON schema. A reply
  that fails validation is `Model reply is not a valid title: <field>: <msg>
  (got '<first 200 characters>')`. The client's own errors (`DetectionError`)
  are re-raised as `TitleError` with the same message, which names Ollama, not
  detection. No retry, as in 0014: at temperature 0 with a fixed seed it would
  repeat the reply.
- **Cross-adapter import.** `adapters/titles` imports the client from
  `adapters/detection`. Moving `OllamaClient` to a shared place is a separate,
  behaviour-neutral change, not part of this item.
- **Order: the title is written before the clip is cut.** A title failure is
  then that clip's failure with no file written, which keeps the manifest's
  exactly-one-of `file` and `error`, and its listed files matching the folder.
  The cost: a clip whose title fails is not rendered, and is rerun with
  `--rank`. With Ollama down every clip fails on its own request (2.5 s in
  R19 d), and nothing renders. There is no reachability check before cutting;
  that would change 0011's check order.
- **Building the writer.** `AppContext` gains `title_writer(settings)`,
  default `build_title_writer`, called only with `--titles` and inside the
  checks before cutting. An empty `CLIPPER_LLM_MODEL` there is `Error: Set
  CLIPPER_LLM_MODEL to an Ollama model name to use --titles`, exit 1, nothing
  written and the manifest unchanged.
- **Manifest (0019).** Run level: `titles`, the flag. Per clip: `title` and
  `hook`, between `captions` and `error`. Both or neither are set, and only
  on an entry with a `file`; a clip whose title succeeded and whose render then
  failed has neither.
- **Console.** A titled clip's line ends with `titles` among its flags, e.g.
  ` (vertical, captions, titles)`, followed by `  Title: ...` and
  `  Hook: ...`, escaped for rich markup.
- **First real run** (manual step R19, 2026-11-03, `qwen3.5:9b`, Ollama
  0.40.13, the 5:14 video, 5 candidates): exit 0 in 32.5 s with `--vertical
  --captions --titles`, including the model load. Every clip got a one-line
  title (41 to 65 characters) and hook (83 to 92), with no hashtags. A second
  run gave identical titles and hooks (0015). One of five titles was over the
  asked length, and rank 5's title and hook inverted the speaker's point.
  Quality is judged by eye, as the Consequences say; the titles are in the
  manual-test reference run.

## Amended by the title prompt PR (after V4)

Three runs of `render --titles` and `drafts` (R19, D8, D9) gave titles up to
76 characters and hooks up to 117 against the "about 60" and "about 100"
asked for, one hook repeating a swear word from the speech, and a title and
hook that turned the speaker's point into its opposite.

- **The prompt changed; nothing else did.** It now asks for a title of at
  most 8 words and a one-sentence hook of at most 15 words, says to keep the
  speaker's meaning and not to turn a claim into its opposite, and forbids
  emoji and swear words "of any kind, not even mild ones such as "ass",
  "damn" or "hell"". The limits are in words because the model keeps to them
  and does not keep to character counts. Validation is unchanged: one line,
  not empty, length not checked.
- **Measured** on 2026-11-03 with a scratch script that ran the real
  `OllamaTitleWriter` (`qwen3.5:9b`, Ollama 0.40.13) on `clip_text` for the
  heuristic's 5 candidates of `test_input.mp4` and top 30 of
  `oFbD0Ot7nQ8.mp4`, from cached transcripts, once per prompt:

  | 35 clips | Old prompt | New prompt |
  |---|---|---|
  | Titles: characters (median, max) | 50, 70 | 33, 47 |
  | Titles: words (median, max) | 10, 13 | 6, 9 |
  | Titles over 60 characters | 7 | 0 |
  | Hooks: characters (median, max) | 84, 123 | 58, 80 |
  | Hooks: words (median, max) | 17, 25 | 11, 15 |
  | Hooks over 100 characters | 6 | 0 |
  | Titles or hooks with a swear word | 1 | 1 |
  | Time | 26.7 s | 22.2 s |

  The old prompt gave the same numbers on two runs. A first draft of the new
  prompt, without the named examples, gave the same lengths except one hook
  of 106 characters.
- **Swearing is not fixed.** On 12 clips of the long stream whose speech
  swears, every prompt tried, old, first draft and new, swore in 1: the same
  clip, titled "...Hire His Ass...". Naming the word in the prompt did not
  change it. It is recorded, not handled in code: posting is manual, so every
  title is read first. A rewrite request on a listed word, or masking it, is
  the next step if real use shows it often.
- **Meaning is not fixed either**, judged by eye. The inverted title on
  `test_input.mp4` rank 5 now reads "Why You Should Never Ring The Bell", but
  its hook still misstates the speaker, and two long-stream hooks may add
  details the clip does not contain ("an easy birdie", "moved to tears"),
  unverified without watching the clips.
- Manual step R19's pass criteria now check the word limits.

## Amended by the review page's Save PR (V5 item 3c)

- **The maintainer may change or clear a title in the review page** (0024).
  `ManifestClip` no longer requires `title` and `hook` together, nor a
  rendered file for them: a typed title sits beside the model's hook, and an
  emptied title box clears the title and keeps the hook. `render --titles`
  still writes both together on a rendered clip, as before.

## Revisit when

- Titles need a different model from the detector.
- The maintainer wants the hook on screen.
- Titles run past the asked length often enough to matter: add a cap.
- Swear words or changed meaning in titles show up often in real use: add a
  rewrite request for listed words, or try another model.

## Related

- 0013: Choose the detector with a CLI option (the factory trigger)
- 0014: Local LLM detector via Ollama
- 0015: Determinism is per detector
- 0019: A folder per video
