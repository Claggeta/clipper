# Clipper

AI-assisted tool that finds clip-worthy moments in long-form video.

**Status:** V1 works locally as a command-line tool (v0.1.0). Clip rendering,
captions, uploads and analytics are not built yet.

## What it does

Given a local video file, Clipper:

1. Extracts the audio with ffmpeg
2. Transcribes it with faster-whisper
3. Scores sliding windows of the transcript and picks the top 5 clip-worthy
   timestamps, each with a score and the reasons behind it
4. Prints a table and saves the result as JSON

Everything runs locally. See [docs/roadmap.md](docs/roadmap.md) for what
comes next.

## Requirements

- Windows, macOS, or Linux
- [Git](https://git-scm.com)
- [uv](https://docs.astral.sh/uv/) (installs Python 3.12 and the dependencies)
- [ffmpeg](https://ffmpeg.org) available on your PATH. Check with
  `ffmpeg -version`.

The first run downloads the Whisper model, so it needs network access once.

## Install

```powershell
git clone https://github.com/claggeta/clipper.git
cd clipper
uv sync
```

## Usage

```powershell
uv run clipper analyze path\to\video.mp4
```

Add `-v` / `--verbose` to see progress logging.

The command prints a table of the top candidates and writes the full result
to `<output dir>/<video name>.json` (by default `data/output/`). Candidates
are ranked by score, not by time. Times are `H:MM:SS`.

If something fails, for example the file has no audio stream or ffmpeg
cannot read it, the command prints `Error: ...` and exits with code 1.

Transcripts are cached in `data/cache/`, so analysing the same video again
skips transcription.

## Configuration

Settings are read from environment variables. A `.env` file is not read.
Relative paths resolve against the current directory, so run Clipper from the
repo root or set absolute paths.

| Variable               | Default       | Meaning                                |
|------------------------|---------------|----------------------------------------|
| `CLIPPER_WHISPER_MODEL`| `base`        | faster-whisper model size              |
| `CLIPPER_TOP_N`        | `5`           | Number of candidates to report         |
| `CLIPPER_CACHE_DIR`    | `data/cache`  | Where transcripts are cached           |
| `CLIPPER_OUTPUT_DIR`   | `data/output` | Where result JSON is written           |

For example, in PowerShell:

```powershell
$env:CLIPPER_WHISPER_MODEL = "small"
uv run clipper analyze path\to\video.mp4
```

## Sample output

A 5:14 speech video (Admiral McRaven's morning motivation speech), analysed
on CPU with the Whisper `base` model:

```text
                            Top clips: test_input.mp4
┏━━━┳━━━━━━━━━┳━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Start   ┃ End     ┃ Score ┃ Why                                            ┃
┡━━━╇━━━━━━━━━╇━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ 0:00:32 │ 0:01:13 │ 0.26  │ strong words: never; fast pace (3.1 words/sec) │
│ 2 │ 0:03:17 │ 0:04:00 │ 0.23  │ strong words: never; fast pace (2.9 words/sec) │
│ 3 │ 0:02:33 │ 0:03:15 │ 0.17  │ strong words: best; fast pace (2.4 words/sec)  │
│ 4 │ 0:01:47 │ 0:02:26 │ 0.09  │ fast pace (2.7 words/sec)                      │
│ 5 │ 0:04:02 │ 0:04:33 │ 0.03  │ fast pace (2.2 words/sec)                      │
└───┴─────────┴─────────┴───────┴────────────────────────────────────────────────┘
Saved: data\output\test_input.json
```

A cold run (no cached transcript, model already downloaded) took 14.3 s.

## Known limitations

- **Weak signals on Whisper `base` output.** The `base` model omits much
  punctuation, so the question and exclamation signals rarely fire. Scores
  cluster low (above, 0.03 to 0.26) and the pace signal carries most of the
  weight. This is signal quality, not a bug, and it is the baseline future
  approaches are measured against.
- **Windows ffmpeg exit codes.** When ffmpeg fails, its exit code is shown
  as an unsigned 32-bit number on Windows, for example `exit 3199971767`
  for invalid data. The error message itself is readable.
- **Long videos are unmeasured.** Only the 5:14 video above has been timed.
  Transcription time should scale roughly with audio length.
- **Output name collisions.** The JSON file is named after the video's file
  name without its extension, so two videos with the same name overwrite
  each other's output.

## Documentation

- [Vision](docs/vision.md)
- [Roadmap](docs/roadmap.md)
- [Architecture](docs/architecture.md)
- [Decision records](docs/decisions/)
- [Manual test checklist](docs/manual-testing.md) (the ffmpeg and Whisper
  checks CI cannot run)

## Development

Install the git hooks once:

```powershell
uv run pre-commit install
```

Run the same checks that CI runs:

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

Commits follow [Conventional Commits](https://www.conventionalcommits.org)
(`feat:`, `fix:`, `docs:`, `chore:`, `test:`). Work happens on short-lived
branches and is merged through pull requests.

## License

To be decided.
