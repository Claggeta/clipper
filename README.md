# Clipper

AI-assisted tool that finds clip-worthy moments in long-form video.

**Status:** V1 and V2 work locally as a command-line tool (v0.2.0). `analyze`
finds the clip-worthy moments and `render` cuts them into video files,
optionally with captions. Uploads and analytics are not built yet.

## What it does

Given a local video file, Clipper:

1. Extracts the audio with ffmpeg
2. Transcribes it with faster-whisper
3. Scores sliding windows of the transcript and picks the top 5 clip-worthy
   timestamps, each with a score and the reasons behind it
4. Prints a table and saves the result as JSON

Then `clipper render` reads that JSON and the original video and writes one
video file per candidate, optionally with the transcript burned in as captions.

Everything runs locally. See [docs/roadmap.md](docs/roadmap.md) for what
comes next.

## Requirements

- Windows, macOS, or Linux
- [Git](https://git-scm.com)
- [uv](https://docs.astral.sh/uv/) (installs Python 3.12 and the dependencies)
- [ffmpeg](https://ffmpeg.org) available on your PATH, with `ffprobe` (it
  ships with ffmpeg). Check with `ffmpeg -version`. Captions need a build that
  includes libass.

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

### Rendering clips

After `analyze`, cut the clips from the saved JSON and the original video:

```powershell
uv run clipper render path\to\video.mp4 data\output\video.json
```

This renders every candidate. Options:

- `--rank N` renders only that rank, the 1-based position in the JSON (the
  table's `#` column). Repeat it for several, for example `--rank 1 --rank 5`.
  Clips are rendered in ascending rank order whatever order you give.
- `--captions` burns the transcript text into each clip, in one fixed style. A
  clip with no speech in range is rendered without captions and a note is
  printed.

Clips are written to `<clips dir>/<video name>_<HHMMSS>-<HHMMSS>.mp4` (by
default `data/clips/`) and re-encoded as H.264 video and AAC audio. The name
comes from the clip's span, not its rank, so rendering the same candidate again
overwrites the same file. A captioned clip has the same name as a plain one, so
`--captions` overwrites a plain clip of the same span.

The video's file name must match the one the JSON was saved for. Problems found
before any cutting (a bad JSON, a name mismatch, a rank out of range, a
candidate past the end of the video, no video stream) print `Error: ...`, write
nothing and exit 1. If one clip fails while others succeed, the rest are still
rendered, each failure is printed, and the command exits 1.

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
| `CLIPPER_CLIPS_DIR`    | `data/clips`  | Where rendered clips are written       |

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

Rendering clips 1 and 5 of that result with captions, from the repo root:

```text
> uv run clipper render data\test_input.mp4 data\output\test_input.json --captions --rank 1 --rank 5
Rendered #1 0:00:32-0:01:13 data\clips\test_input_000032-000114.mp4 (captions)
Rendered #5 0:04:02-0:04:33 data\clips\test_input_000402-000434.mp4 (captions)
Rendered 2 of 2 clips to data\clips
```

The console line shows both ends rounded down, while the file name rounds the
end up, so a clip ending at 73.8 s prints `0:01:13` and is named `...-000114.mp4`.
Rendering all 5 candidates took 12.0 s without captions and 12.05 s with them
(Windows, same video, one run each).

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
  each other's output. Rendered clips are named the same way, so two videos
  with the same name and overlapping spans overwrite each other's clips.
- **The video check is by name only.** `render` checks that the video's file
  name matches the one in the JSON. A different video with the same name is cut
  without a warning.
- **Captions need libass.** An ffmpeg build without it fails every clip with
  ffmpeg's own message. Only one build (gyan.dev 9.0.2, with libass) has been
  tried.
- **Captions are segment-level and fixed in style.** A long Whisper segment
  makes a large caption block, and a segment that crosses a clip's edge shows
  its whole sentence for the part inside the clip. The characters
  `{ } < > \` are removed from caption text because the renderer reads them as
  markup, so `5 > 3` shows as `5 3`.
- **`render` has no `-v`.** Only the 10-line ffmpeg failure message is
  available.

## Documentation

- [Vision](docs/vision.md)
- [Roadmap](docs/roadmap.md)
- [Architecture](docs/architecture.md)
- [Decision records](docs/decisions/)
- [Manual test checklist](docs/manual-testing.md) (the ffmpeg, Whisper,
  rendering and caption checks CI cannot run)
- [Release notes](docs/releases/v0.2.0.md)

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
