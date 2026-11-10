# Clipper

AI-assisted tool that finds clip-worthy moments in long-form video.

**Status:** V1 to V4 work locally as a command-line tool (v0.4.0).
`clipper drafts` takes a YouTube or Twitch URL, or a local file, to a folder of
vertical clips with word-by-word captions and a suggested title and hook for
each. `fetch`, `analyze`, `render` and `eval` are also separate commands, and
`labels` turns a Twitch VOD's community clips into a labels file for `eval`.
Posting, uploads and analytics are not built.

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
- For `fetch`, `drafts` from a URL, and `labels` only:
  [yt-dlp](https://github.com/yt-dlp/yt-dlp) on your PATH, e.g.
  `uv tool install yt-dlp`, then `uv tool update-shell` once.

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

Add `-v` / `--verbose` to see progress logging. `--detector` chooses the
detector: `heuristic`, the default, or `llm`, which asks a local model served
by [Ollama](https://ollama.com) to score each window. `llm` needs Ollama
running with the model pulled (`ollama pull qwen3.5:9b`, or set
`CLIPPER_LLM_MODEL` to another model), and it is recorded with its model, e.g.
`llm:qwen3.5:9b`. It is not yet better than the heuristic at picking the top
five on a long video, so `heuristic` stays the default here (`drafts`
defaults to `llm`); see
[ADR 0014](docs/decisions/0014-local-llm-detector-via-ollama.md) and the
manual-test steps.

The command prints a table of the top candidates and the detector's name, and
writes the full result, including the detector's name, to
`<output dir>/<video name>.json` (by default `data/output/`). Candidates
are ranked by score, not by time. Times are `H:MM:SS`.

If something fails, for example the file has no audio stream or ffmpeg
cannot read it, the command prints `Error: ...` and exits with code 1.

Transcripts are cached in `data/cache/`, so analysing the same video again
skips transcription. Transcripts include the time of each word. Transcripts
cached by v0.3 and earlier have no word times, so the first `analyze` of a
video after upgrading transcribes it again.

### Fetching a video by URL

Instead of a local file, you can start from one YouTube video or Twitch VOD:

```powershell
uv run clipper fetch "https://www.youtube.com/watch?v=<id>"
uv run clipper analyze data\videos\<id>.mp4
```

`fetch` downloads with yt-dlp to `<videos dir>/<id>.mp4` (by default
`data/videos/`), named by the platform's video ID (`v<number>` for Twitch), at
up to 1080p, and prints `Saved: <path>`. If that file exists it is reused.
One video per call: playlist, channel, Twitch live and clip URLs are refused.
It uses no cookies or logins, so videos that need an account fail.

**Downloading from YouTube or Twitch is against their terms of service.**
Clipper is a personal tool; you are responsible for having the right to
download a video. See
[ADR 0020](docs/decisions/0020-fetch-a-video-by-url.md) for the clauses.

### Rendering clips

After `analyze`, cut the clips from the saved JSON and the original video:

```powershell
uv run clipper render path\to\video.mp4 data\output\video.json
```

This renders every candidate. Options:

- `--rank N` renders only that rank, the 1-based position in the JSON (the
  table's `#` column). Repeat it for several, for example `--rank 1 --rank 5`.
  Clips are rendered in ascending rank order whatever order you give.
- `--captions` burns captions into each clip, a few words at a time, in bold
  white with the word being spoken in yellow. A clip with no speech in range
  is rendered without captions and a note is printed. An analysis saved by
  v0.3 or earlier has no word times: its captions show whole segments, with a
  note. Run `analyze` again for word-by-word captions; that re-transcribes the
  video once and may move the candidates.
- `--vertical` makes a 1080x1920 (9:16) clip for short-form platforms: the
  whole frame, scaled to fit the width, centred on a blurred copy of itself.
  Nothing is cut off. Black bars built into the source stay in the picture.
  Captions are drawn on the vertical frame. `--style face` crops a
  full-height 9:16 window that follows the largest face instead (faces
  found with OpenCV, cached under the cache dir); a clip with no face is
  skipped with a note. It suits podcasts and webcam streams; on groups it
  cuts people out.
- `--titles` suggests a title and a hook line per clip with a local model
  through [Ollama](https://ollama.com), the same server and model as
  `--detector llm` (`CLIPPER_LLM_MODEL`, default `qwen3.5:9b`). They are
  printed under each clip and written to the manifest, not drawn on the video.
  The model sees what is said in the clip and, for a fetched video, its channel
  and title (from the `<id>.info.json` that `fetch` saves). `--about "<text>"`
  adds names the model should use, such as `--about "Ron (stableronaldo)"`;
  metadata names the channel, not the people. A clip with no speech in range
  gets no title, with a note. If the title fails (Ollama not running, model not
  pulled, a bad reply), that clip is not rendered and counts as a failure.

Clips are written to a folder per video,
`<clips dir>/<video name>/<video name>_<HHMMSS>-<HHMMSS>.mp4` (by default
`data/clips/`), and re-encoded as H.264 video and AAC audio. The name comes
from the clip's span, not its rank, so rendering the same candidate again
overwrites the same file. A captioned clip has the same name as a plain one, so
`--captions` overwrites a plain clip of the same span. Clips rendered before
v0.4 stay in the flat `data/clips/` folder.

Each run also writes `manifest.json` in the video's folder, replacing the one
before. It records the video, the analysis JSON, the detector, the flags, and
one entry per selected clip with its rank, span, score, reasons, file name,
which captions were burned in (`words`, `segments` for an analysis without word
times, or `null`), the title and hook with `--titles`, and the error if it
failed. A `--rank` run lists only the ranks it rendered. One entry from a real
run with `--vertical --captions --titles` (`qwen3.5:9b`):

```json
{
  "rank": 1,
  "start_seconds": 32.21,
  "end_seconds": 73.0,
  "score": 0.26186565334640843,
  "reasons": [
    "strong words: never",
    "fast pace (3.1 words/sec)"
  ],
  "file": "test_input_000032-000113.mp4",
  "captions": "words",
  "title": "Why Navy SEALs Inspect Your Bed Every Morning",
  "hook": "If you can't do the little things right, you'll never be able to do the big things right.",
  "error": null
}
```

The video's file name must match the one the JSON was saved for. Problems found
before any cutting (a bad JSON, a name mismatch, a rank out of range, a
candidate past the end of the video, no video stream) print `Error: ...`, write
nothing and exit 1. If one clip fails while others succeed, the rest are still
rendered, each failure is printed and listed in the manifest, and the command
exits 1.

### One command: from a URL or file to drafts

```powershell
uv run clipper drafts "https://www.youtube.com/watch?v=<id>"
uv run clipper drafts path\to\video.mp4
```

`drafts` runs `fetch` (for a URL), `analyze` and `render` in one go and ends
with `Drafts: <clips dir>/<video name>`: a folder with every candidate as a
vertical, captioned clip, and a suggested title and hook for each in the
manifest. Each clip comes in two styles to pick from: `<name>.mp4` fits the
whole frame, and `<name>.face.mp4` crops on the largest face (a clip with no
face gets only the first). `--style fit` or `--style face` renders one.
Give several sources, or `--list sources.txt` (one per line, `#` for
comments, and `| <about text>` after a source for its own `--about`), to
queue them: every source is checked first, then they run one after another,
a failed one does not stop the rest, and a table at the end lists each
folder or error.
`--no-vertical`, `--no-captions` and `--no-titles` turn those off;
use `--no-titles` when Ollama is not running. `--about` works as for
`render`. `drafts` picks with the `llm` detector and drafts the top 10
by default (see [ADR 0014](docs/decisions/0014-local-llm-detector-via-ollama.md));
`--detector heuristic` is faster and needs no Ollama with `--no-titles`,
and `--top N` sets how many (`CLIPPER_TOP_N` does not apply). `-v` works
as for `analyze`. A bad URL, a missing file or an empty `CLIPPER_LLM_MODEL` stops
it before anything is downloaded or transcribed. If one clip fails, the rest
still render and it exits 1. A URL is subject to the same terms warning as
`fetch`. See [ADR 0022](docs/decisions/0022-one-command-to-drafts.md).

### Evaluating detectors

`eval` measures the detector against moments you labeled by hand. It reruns the
detector on the transcript saved by `analyze`, so nothing is transcribed again:

```powershell
uv run clipper eval data\output\video.json
```

By default it reads `<labels dir>/<video name>.json` (by default
`data/labels/`), named after the video the JSON was saved for. Pass a second
path to use another labels file. A labels file looks like this (constructed
example):

```json
{
  "source": "video.mp4",
  "labels": [
    { "start": "0:50", "end": "2:29", "note": "the story about the bed" },
    { "start": "1:02:03.5", "end": "1:03:00" }
  ]
}
```

- `source` is the video's file name and must match the one in the analysis
  JSON.
- Times are `"M:SS"` or `"H:MM:SS"` strings, with optional fractional seconds.
  Plain numbers are rejected, because `0.50` would load as half a second.
- Labels must be in time order and must not overlap. Touching is allowed.
  `note` is optional, and other keys are rejected.
- An empty `labels` list is valid: nothing in the video is worth cutting.

A candidate hits a label when they overlap by at least half of the shorter of
the two. `eval` prints each label with the candidates that hit it, each
candidate with the labels it hits, then recall (labels hit by at least one
candidate) and precision, as counts and percentages. Precision is one-to-one:
going down the ranking, each candidate claims the first label it hits that a
higher-ranked candidate has not, and a candidate that hits only claimed labels
is a repeat (`16 (repeat of #3)`) and does not count. Recall is `n/a` with no
labels, and precision is `n/a` with no candidates. A label that ends after the
transcript does is noted and still counted. The numbers come from one person's
labels on a few videos, so treat a difference between detectors as a sanity
check, not a measurement.

`--detector` chooses the detector to rerun, as on `analyze`. The default is
`heuristic`.

A bad or missing file, or labels written for a different video, prints
`Error: ...` and exits 1.

### Labels from Twitch clips

For a Twitch VOD, the moments viewers clipped can stand in for hand labels.
List each clip's link and its "Watch full video" link (a VOD URL with `?t=`)
in a links file, one clip per line, with `#` comments allowed:

```text
# data\labels\links\v1234567890.txt (constructed example)
https://www.twitch.tv/somechannel/clip/SomeClipName-AbC123 https://www.twitch.tv/videos/1234567890?t=4h36m45s
https://clips.twitch.tv/OtherClipName-XyZ789 https://www.twitch.tv/videos/1234567890?t=1h44m34s
```

```powershell
uv run clipper labels data\labels\links\v1234567890.txt
```

This writes `<labels dir>/v<VOD number>.json`, the same format as above, for
`v<VOD number>.mp4`, the name `fetch` gives the VOD. Each clip's title and
length are read with yt-dlp, without downloading. `t` marks where a clip ends,
so a clip covers `t` minus its length to `t`; measured on four clips, `t` was
up to 4 s before the real end. Overlapping clips merge into one label whose
note lists their titles. The command prints each clip, then the clips read,
the labels after merging, the minutes covered and the path.

Every line must name the same VOD; a bad line prints `Error:` with the file
and line number, before anything is looked up. An existing labels file is
not replaced unless you pass `--force`. Like `fetch`, reading Twitch with a
tool is against its terms of service
([ADR 0020](docs/decisions/0020-fetch-a-video-by-url.md)).

Clips show what viewers reacted to, not every moment worth cutting, so
precision against them is a lower bound; read recall first. See
[ADR 0012](docs/decisions/0012-evaluate-detectors-against-hand-labels.md).

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
| `CLIPPER_LABELS_DIR`   | `data/labels` | Where `eval` looks for labels files, and `labels` writes them |
| `CLIPPER_VIDEOS_DIR`   | `data/videos` | Where `fetch` saves downloaded videos  |
| `CLIPPER_LLM_URL`      | `http://127.0.0.1:11434` | Ollama server for `--detector llm` |
| `CLIPPER_LLM_MODEL`    | `qwen3.5:9b`  | Ollama model for `--detector llm`      |

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

That output is from v0.2. Since then the clips go to `data\clips\test_input\`,
and a `Manifest:` line follows the last line.

The console line shows both ends rounded down, while the file name rounds the
end up, so a clip ending at 73.8 s prints `0:01:13` and is named `...-000114.mp4`.
Rendering all 5 candidates took 12.0 s without captions and 12.05 s with them
(Windows, same video, one run each).

Evaluating that result against the maintainer's four labels for the same video
(`data/labels/test_input.json`), from the repo root:

```text
> uv run clipper eval data\output\test_input.json
                            Labels: test_input.mp4
┌───┬─────────┬─────────┬────────┬────────────────────────────────────────────┐
│ # │ Start   │ End     │ Hit by │ Note                                       │
├───┼─────────┼─────────┼────────┼────────────────────────────────────────────┤
│ 1 │ 0:00:00 │ 0:00:50 │ -      │ Make your bed: the opener                  │
│ 2 │ 0:00:50 │ 0:02:29 │ #1, #4 │ SEAL bed inspection, struggles apply to    │
│   │         │         │        │ all                                        │
│ 3 │ 0:02:30 │ 0:03:47 │ #2, #3 │ Darkest moment, power of hope, the list    │
│ 4 │ 0:03:48 │ 0:04:35 │ #5     │ Ring the bell                              │
└───┴─────────┴─────────┴────────┴────────────────────────────────────────────┘
                     Candidates
┌───┬─────────┬─────────┬───────┬──────────────────┐
│ # │ Start   │ End     │ Score │ Hits label       │
├───┼─────────┼─────────┼───────┼──────────────────┤
│ 1 │ 0:00:32 │ 0:01:13 │ 0.26  │ 2                │
│ 2 │ 0:03:17 │ 0:04:00 │ 0.23  │ 3                │
│ 3 │ 0:02:33 │ 0:03:15 │ 0.17  │ 3 (repeat of #2) │
│ 4 │ 0:01:47 │ 0:02:26 │ 0.09  │ 2 (repeat of #1) │
│ 5 │ 0:04:02 │ 0:04:33 │ 0.03  │ 4                │
└───┴─────────┴─────────┴───────┴──────────────────┘
Note: label 4 (0:03:48-0:04:35) ends after the end of the transcript (273.8s); no detector can reach the part past it
Detector: heuristic (top 5)
1 video, 4 labels, 5 candidates
Recall: 3 of 4 labels (75%)
Precision: 3 of 5 candidates (60%); 2 more repeat a matched label
Indicative only: one labeler, 1 video, 4 labels (ADR 0012).
```

These four labels cover the whole speech, so every candidate lands inside one,
and precision drops only through repeats: candidates 3 and 4 hit labels that a
higher-ranked candidate already claimed. This run shows that `eval` works, not
how good the detector is. That needs a long video with stretches the
maintainer would not post.

## Known limitations

- **Weak signals on Whisper `base` output.** The `base` model omits much
  punctuation, so the question and exclamation signals rarely fire. Scores
  cluster low (above, 0.03 to 0.26) and the pace signal carries most of the
  weight. This is signal quality, not a bug, and it is the baseline future
  approaches are measured against.
- **Windows ffmpeg exit codes.** When ffmpeg fails, its exit code is shown
  as an unsigned 32-bit number on Windows, for example `exit 3199971767`
  for invalid data. The error message itself is readable.
- **Long videos are slow on CPU.** A 5:04:01 stream took 20:59 to analyse
  cold with the Whisper `base` model on CPU (one run, model already
  downloaded), against 14.3 s for the 5:14 video above. A rerun with the
  transcript cached took 0.67 s. The GPU and larger models are untried.
- **Output name collisions.** The JSON file is named after the video's file
  name without its extension, so two videos with the same name overwrite
  each other's output. Rendered clips go to a folder named the same way, so
  two videos with the same name share a folder and manifest, and overlapping
  spans overwrite each other's clips.
- **The video check is by name only.** `render` checks that the video's file
  name matches the one in the JSON. A different video with the same name is cut
  without a warning.
- **Captions need libass.** An ffmpeg build without it fails every clip with
  ffmpeg's own message. Only one build (gyan.dev 9.0.2, with libass) has been
  tried.
- **Captions have one style.** It was chosen by eye on one video and is not
  tuned. A word that starts before a clip's first frame is not shown. The
  characters `{ } < > \` are removed from caption text because the renderer
  reads them as markup, so `5 > 3` shows as `5 3`.
- **`render` has no `-v`.** Only the 10-line ffmpeg failure message is
  available.

## Documentation

- [Vision](docs/vision.md)
- [Roadmap](docs/roadmap.md)
- [Architecture](docs/architecture.md)
- [Decision records](docs/decisions/)
- [Manual test checklist](docs/manual-testing.md) (the ffmpeg, Whisper,
  rendering, caption and LLM detector checks CI cannot run)
- [Release notes](docs/releases/v0.3.0.md)

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
