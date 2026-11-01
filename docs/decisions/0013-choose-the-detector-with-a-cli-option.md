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

## Related

- 0006, 0011: the private-factory seam
- 0012: `eval`
- 0014: local LLM detector
