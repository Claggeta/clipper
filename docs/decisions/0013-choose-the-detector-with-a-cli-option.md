# 0013: Choose the detector with a CLI option

- Status: Accepted
- Date: 2026-11-01
- Amends: 0006 (clarifies what its rejected switch covers)

## Context

`analyze` builds `HeuristicClipDetector` inline. V3 adds a second real
detector (0014) and `clipper eval` (0012) needs to run either one over the
same transcript. Someone has to choose.

0006 rejected an environment or Settings switch for the transcriber, because
it put a **test double** on the production path and let a user silently get
fixed sample output. A choice between two real detectors is a different thing.

## Decision

1. `analyze` and `eval` take `--detector heuristic|llm`. The default is
   `heuristic`, so existing behaviour does not change.
2. `cli/main.py` composes the detector in a private `_build_detector(settings,
   name)`, patched by tests the same way as `_build_transcriber`,
   `_build_renderer` and `_build_probe`. This is the fourth injected
   dependency. The move to a context object (0006, 0011) is still
   outstanding, and it is decided in this ADR's item, not here.
3. No fake or test detector is selectable by name. Tests patch the factory.
4. Which detector produced a result is recorded in the existing `reasons`
   only. Whether `AnalysisResult` should record the detector name is open and
   is decided in the item that implements this ADR, because it is a domain
   change.
5. An unknown name is a usage error from the CLI.

## Alternatives considered

- **An env var or `Settings` field.** Rejected for the choice itself: a
  per-run decision belongs on the command line. A default may move to settings
  later.
- **Selecting from a registry by string in `core/`.** Rejected. `core/` cannot
  name adapters, and a registry is more machinery than two classes need.
- **A separate `analyze-llm` command.** Rejected. It duplicates the command.

## Consequences

- (+) The default path is unchanged, and `eval` can compare both detectors.
- (+) Layering is kept: only `cli/` names concrete classes.
- (-) A fourth private name that tests depend on. A rename fails loudly.
- (-) `--detector llm` needs a running local model server (0014). A missing
  server must be a clear error, not a silent fall back to the heuristic.

## Revisit when

- A third real detector appears, or more than four factories exist. Move to the
  context object then.

## Amended by the `--detector` PR (V3 item 4)

- `AnalysisResult` records the detector in a new field, `detector: str | None`,
  set to the `--detector` value. This replaces decision 4's "in the existing
  `reasons` only": the heuristic's reasons describe its signals and never named
  the detector, so nothing saved before this said which detector made a result.
  Once two detectors write to the same `<output dir>/<stem>.json`, the file
  must say which one it holds.
- The field defaults to `None`, so JSON saved before it loads unchanged.
  `None` means "not recorded", not "heuristic": `domain/` does not name
  adapters, and old files cannot show which detector made them.
- `AnalysisPipeline` takes the name as a keyword argument, `detector_name`, and
  writes it into the result. The `ClipDetector` Protocol does not gain a `name`:
  the choice the user made is what is recorded, and every detector and test
  double would otherwise need the attribute.
- `render` and `eval` neither show nor check the saved name. `render` only cuts
  saved spans (0015), and `eval` reruns the detector it is given and ignores
  the saved candidates.
- `analyze` prints `Detector: <name>` after the table. `eval` prints
  `Detector: <name> (top N)`, as before. The output file name stays
  `<stem>.json` whichever detector runs, so a later run with another detector
  overwrites it. The transcript is cached and `eval` reruns detectors from any
  saved JSON, so little is lost.
- `--detector` takes a `DetectorName` enum in `cli/main.py`. Until the LLM
  detector lands (V3 item 7) its only value is `heuristic`, and any other
  value, `llm` included, is Typer's usage error (exit 2). This is decision 5;
  decision 1's `llm` arrives with item 7 rather than as an option that always
  fails.
- The factory is `_build_detector(settings, name)`. Both `analyze` and `eval`
  call it. Tests patch it and return a stub detector defined in the test file.
- The context object (0006, 0011) is decided here: not now. After this item
  there are four factories, three commands and one real detector. 0011's
  trigger (a third command) was passed by `eval`, and 0013 deferred the
  decision to this item. The private factories stay, and this ADR's "revisit
  when" (more than four factories, or a third real detector) replaces the
  triggers in 0006 and 0011.

## Related

- 0006, 0011: the private-factory seam
- 0012: `eval`
- 0014: local LLM detector
