# 0022: One command from a URL or file to a folder of drafts

- Status: Accepted
- Date: 2026-11-03

## Context

V4's interface ends in "one command from a URL to that folder" (vision
success criterion 5). Today that is three commands: `fetch` (0020) for a URL,
`analyze`, then `render --vertical --captions --titles` (0011, 0016, 0017,
0018, 0019). Each works and is tested alone. The maintainer's "unattended"
means: give a URL, come back to rendered clips in a folder, choose which to
post.

0011 rejected `analyze --render` because it couples two stages inside one
command and makes `analyze` do more than it says. 0011 is about `render`
alone, so a command that chains three stages, with defaults and a failure
order across them, gets its own record.

## Decision

1. **`clipper drafts <source>`**, a new command. `<source>` starting with
   `http://` or `https://` is a URL and goes through `fetch`'s rules (0020);
   anything else is a local video file. `fetch`, `analyze` and `render` stay
   as they are.
2. **Steps, in order:**
   1. For a URL, fetch to `<videos dir>/<id>.mp4`, reusing an existing file.
   2. Analyze: the same pipeline, transcript cache and output as `analyze`,
      printing the candidates table and saving `<output dir>/<stem>.json`.
   3. Render every candidate into `<clips dir>/<stem>/` with its manifest
      (0019), whose `analysis` is the JSON just saved.

   The last line is `Drafts: <folder>`.
3. **Defaults are the V4 drafts**: vertical, captions and titles are on.
   `--no-vertical`, `--no-captions` and `--no-titles` turn each off, for
   example `--no-titles` when Ollama is not running. `--detector` and `-v`
   are passed through as in `analyze`. There is no `--rank`: every candidate
   is rendered, as many as `CLIPPER_TOP_N`; `render --rank` redoes single
   clips.
4. **Failure order:**
   - Before anything is downloaded or transcribed: a refused URL, a missing
     file, and, with titles on, an empty `CLIPPER_LLM_MODEL`. The title
     writer is built first, so a long transcription is not wasted on a
     setting.
   - A fetch or analysis failure is `Error:` and exit 1, with nothing
     rendered and no JSON written.
   - Rendering follows 0011 and 0018: one clip's failure, title included,
     does not stop the rest; the manifest lists it; the exit code is 1.
   - There is no Ollama reachability check before cutting, as in 0018.
5. **One code path per step.** The bodies of `analyze` and `render` are first
   moved into private functions in `cli/main.py`, as a behaviour-neutral
   change with every existing test unchanged, and `drafts` calls the same
   functions. The render loop is mostly console and manifest work, so it
   stays in the composition root rather than moving to a `core/` pipeline.
6. A manual-test step runs `drafts` on a short URL end to end, and times it
   on the 5:04:01 stream from a local file with a cold cache, which is
   criterion 5's long-stream time.

## Alternatives considered

- **`analyze --render` or `render <url>`.** Couples stages inside a command
  that says it does one thing (0011). Rejected.
- **Accept a URL in `analyze`.** Smaller, but still leaves `render` as a second
  command, so not one command to the folder. Rejected.
- **A `DraftsPipeline` in `core/`** composing a downloader, transcriber,
  detector, renderer and title writer. Testable without the CLI, but needs the
  render loop's notes, manifest and failure handling moved out of the CLI, a
  broad refactor. Not now; revisit if a second front end appears.
- **A shell script chaining the three commands.** No code, but no shared
  failure order, no early settings check, and per-platform scripts. Rejected.
- **Names**: `run` is vague; `clip` reads as a single clip. `drafts` names the
  output.

## Consequences

- (+) One command for daily use, with the same outputs as running the three
  steps by hand.
- (+) Each step is still a command of its own, for reruns and debugging.
- (-) A fourth command in `cli/main.py`, which grows. The extraction keeps one
  copy of each step's logic.
- (-) With titles on and Ollama down, every clip fails after a full analysis.
  The transcript cache makes the rerun cheap.
- (-) A long stream still takes about 21 minutes to transcribe on the CPU
  (0021) before the first clip renders.

## Amended by the `drafts` PR (V4 item 8)

- **Extraction done** (#109, behaviour-neutral): `_analyze_video`,
  `_plan_render` and `_cut_clips` in `cli/main.py`, with no test changed and
  every command's `--help` byte-identical. This PR also moves `fetch`'s body
  into `_fetch_video`, with its output unchanged, so `drafts` fetches through
  the same code.
- **URL or file**: a `<source>` starting with `http://` or `https://`, in
  any case, is a URL. A file that does not exist is `Error: Input file not
  found: <path>`, the pipeline's own message, raised before the title writer
  is built.
- **Order inside the checks**: refused URL or missing file, then the title
  writer (an empty `CLIPPER_LLM_MODEL`), then the fetch, the analysis, and
  `render`'s checks before cutting. `Drafts: <folder>` is printed after the
  manifest even when a clip failed, then the exit code is 1.
- **Manual steps D8 to D10** (Fetch section). "Ollama not running" is
  checked by pointing `CLIPPER_LLM_URL` at a port nothing listens on, which
  Clipper cannot tell from a stopped server and which leaves the user's
  Ollama alone.
- **First real run**, 2026-11-03, run by the assistant in a child process:
  an 8:53 YouTube URL to 5 titled vertical drafts in 71.5 s; the 5:04:01
  stream from a local file, cold, in **1,378 s (22:58)**, against 1,260 s for
  a cold `analyze` alone (vision criterion 5); with Ollama unreachable every
  clip failed its title in 10.8 s, and `--no-titles` rendered all 5 in
  26.6 s.

## Revisit when

- A second front end (a scheduler, a watch folder, a UI) needs the same
  chain: move it to `core/`.
- The maintainer wants a subset of candidates or a different `--top-n` per
  run.
- Fetching or rendering needs to resume a half-finished run.

## Related

- 0011: `render` command shape and failure policy
- 0013: the context object the steps' adapters come from
- 0016, 0017, 0018, 0019: vertical, captions, titles, the folder per video
- 0020: fetch
- 0021: GPU transcription measured, not adopted
