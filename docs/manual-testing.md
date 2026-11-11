# Manual test checklist: ffmpeg, Whisper, rendering and the LLM detector

CI cannot run the real ffmpeg binary or a real Whisper model. Its tests mock
`subprocess.run` for ffmpeg, use `FakeTranscriber` for the CLI, and the one
integration test decodes a generated WAV through PyAV only. This checklist
covers what is left. It closes the gap recorded in
[ADR 0004](decisions/0004-report-ffmpeg-errors.md) and
[ADR 0005](decisions/0005-classify-no-audio-in-extractor.md): nothing in CI
confirms that `-hide_banner -loglevel error` leaves the cause line on stderr,
or that `does not contain any stream` is still ffmpeg's wording.

The [Rendering](#rendering) section covers `clipper render` the same way. The
render tests pass a renderer and a probe of their own through `AppContext`
(ADR 0013), so nothing in CI runs the real `ffprobe` JSON parsing or the real
clip encode.

The [Captions](#captions) section covers `clipper render --captions`. The
renderer tests mock `subprocess.run`, so nothing in CI runs the real `subtitles`
filter.

The [LLM detector](#llm-detector) section covers `--detector llm`. Its tests
use a fake generator and a mocked HTTP call, so nothing in CI talks to a real
Ollama server or model. Step R19 does the same for `render --titles`.

The [Fetch](#fetch) section covers `clipper fetch`. Its tests mock yt-dlp, so
nothing in CI downloads.

The [Labels from Twitch clips](#labels-from-twitch-clips) section covers
`clipper labels`. Its tests mock yt-dlp and pass a fake clip lookup to the
CLI, so nothing in CI reads Twitch.

## When to run it

- Before tagging a release.
- After upgrading ffmpeg, `faster-whisper`, `av` or `huggingface-hub`
  (see [ADR 0003](decisions/0003-pin-transcription-dependencies.md)).
- After any change to `src/clipper/adapters/media/ffmpeg.py`,
  `src/clipper/adapters/transcription/`, or the ffmpeg command line.
- After any change to `src/clipper/adapters/rendering/`,
  `src/clipper/adapters/media/ffprobe.py`, `src/clipper/adapters/results/`,
  or the `render` command. Run the [Rendering](#rendering) section.
- After any change to `src/clipper/core/captions.py`, the `subtitles` filter or
  its style in `adapters/rendering/ffmpeg.py`, or after changing to an ffmpeg
  build. Run the [Captions](#captions) section.
- After any change to `src/clipper/adapters/detection/llm.py` or `ollama.py`
  (the prompt included), after upgrading Ollama, or before changing
  `CLIPPER_LLM_MODEL`'s default. Run the [LLM detector](#llm-detector)
  section, and step R19 (titles share the client and the model).
- After any change to `src/clipper/adapters/titles/` (the prompt included) or
  `clip_text` in `core/captions.py`. Run step R19.
- After any change to `src/clipper/adapters/download/` or
  `core/sources.py`, after upgrading yt-dlp, or when a `fetch` that used to
  work fails. Run the [Fetch](#fetch) section.
- After any change to the `drafts` command, or to the shared analyze, render
  or fetch steps in `cli/main.py`. Run steps D8 to D10a.
- After any change to `core/labeling.py`, `YtDlpClipLookup` in
  `adapters/download/ytdlp.py`, `save_labels` or `load_links`, or the `labels`
  command, and after upgrading yt-dlp. Run the
  [Labels from Twitch clips](#labels-from-twitch-clips) section.

Run it in a fresh PowerShell window from the **repo root**. Several steps edit
environment variables or `PATH` for the current session only.

## Prerequisites

- `uv sync` has been run.
- `ffmpeg` and `ffprobe` are on `PATH`. Record the version:

  ```powershell
  ffmpeg -version | Select-Object -First 1
  ```

- Network access, for the first download of the `tiny` model (step W4).
- A **source video** with speech and an audio track, at least 30 seconds long.
  Any speech clip works. Nothing is committed to the repo, and `data/` is
  gitignored.

## Setup

Everything below is generated from the source video. Caches and outputs go to
a scratch folder, so `data/` is not touched.

```powershell
$Src = "path\to\speech.mp4"                       # your source video
$S   = Join-Path $env:TEMP "manual-test-clipper"  # must not start with "clipper-"
New-Item -ItemType Directory -Force $S, "$S\with space" | Out-Null

# Keep rich from wrapping messages at 80 columns, which splits phrases.
$env:COLUMNS = "500"
$env:CLIPPER_CACHE_DIR  = "$S\cache"
$env:CLIPPER_OUTPUT_DIR = "$S\out"

# Test media
ffmpeg -hide_banner -loglevel error -y -t 30 -i $Src -c copy "$S\normal.mp4"
ffmpeg -hide_banner -loglevel error -y -t 30 -i $Src -an -c copy "$S\no-audio.mp4"
ffmpeg -hide_banner -loglevel error -y -t 30 -i $Src -t 30 -i $Src `
  -map 0:v:0 -map 0:a:0 -map 1:a:0 -c copy "$S\multi-audio.mp4"
Set-Content -Path "$S\bad.mp4" -Value "this is not a video" -Encoding ascii
$b = [IO.File]::ReadAllBytes("$S\normal.mp4")
[IO.File]::WriteAllBytes("$S\truncated.mp4", $b[0..([int]($b.Length / 3))])
Copy-Item "$S\normal.mp4" "$S\with space\normal clip.mp4"

# Helper: runs the CLI, keeps the exit code and text, and prints both.
function Invoke-Clipper {
  param([string]$Path, [switch]$V)
  $flag = if ($V) { "-v" } else { "" }
  cmd /c "uv run clipper analyze $flag `"$Path`" > `"$S\last.txt`" 2>&1"
  $script:Code = $LASTEXITCODE
  $script:Text = Get-Content "$S\last.txt" -Raw -Encoding UTF8
  "exit $Code"
  $Text
}
```

Check the streams you generated. Expect `video,audio` for `normal.mp4`,
`video` for `no-audio.mp4` and `video,audio,audio` for `multi-audio.mp4`:

```powershell
foreach ($f in "normal","no-audio","multi-audio") {
  "$f : " + ((ffprobe -v error -show_entries stream=codec_type -of csv=p=0 "$S\$f.mp4") -join ",")
}
```

Console note: judge table rendering in the terminal itself. Reading captured
output back with the wrong encoding shows box-drawing characters as mojibake,
which is a capture artefact and not a Clipper failure.

## ffmpeg

### F1. Quiet flags leave the cause line on stderr

This is the check ADR 0004 and ADR 0005 say belongs here. It runs the same
command the adapter builds, with and without the quiet flags.

```powershell
function Invoke-Ffmpeg($flags, $in) {
  cmd /c "ffmpeg $flags -y -i `"$in`" -map 0:a:0? -ac 1 -ar 16000 `"$S\a.wav`" 2> `"$S\ff.txt`""
  "exit $LASTEXITCODE"
  Get-Content "$S\ff.txt"
}
Invoke-Ffmpeg "-hide_banner -loglevel error" "$S\no-audio.mp4"   # quiet
Invoke-Ffmpeg "" "$S\no-audio.mp4"                                # noisy
Invoke-Ffmpeg "-hide_banner -loglevel error" "$S\bad.mp4"         # quiet
Invoke-Ffmpeg "" "$S\bad.mp4"                                     # noisy
```

Pass:

- Exit is non-zero in all four runs.
- The quiet no-audio run contains `Output file does not contain any stream` and
  does **not** contain `configuration:` or `built with`.
- The quiet `bad.mp4` run contains `moov atom not found` and
  `Invalid data found when processing input`, and no banner.
- The noisy runs contain the banner, and the cause line is among the last few
  lines. This shows why the flags are load-bearing.

Fail: the quiet runs contain a banner, or the cause line is missing. Then
ADR 0004 and ADR 0005 no longer hold for this ffmpeg build. Fix the adapter
before releasing.

### F2. Normal video extracts to 16 kHz mono WAV

```powershell
ffmpeg -hide_banner -loglevel error -y -i "$S\normal.mp4" -map 0:a:0? -ac 1 -ar 16000 "$S\a.wav"
"exit $LASTEXITCODE"
ffprobe -v error -show_entries stream=sample_rate,channels -of csv=p=0 "$S\a.wav"
```

Pass: exit 0, no stderr output, and ffprobe prints `16000,1`.

### F3. No audio stream raises `NoAudioStreamError`

```powershell
Invoke-Clipper "$S\no-audio.mp4"
```

Pass: exit 1. The text starts with `Error:` and contains the full path,
`has no audio stream` and `use a file with an audio track`. It does **not**
start with `ffmpeg failed`.

Fail: the generic `ffmpeg failed (exit ...)` message. ffmpeg's wording changed
and the marker in `ffmpeg.py` no longer matches (ADR 0005).

### F4. Invalid file gives a readable cause

```powershell
Invoke-Clipper "$S\bad.mp4"
$Text -match 'ffmpeg failed \(exit'
$Text -match 'moov atom not found'
$Text -match 'Invalid data found when processing input'
$Text -match 'configuration:|built with'
```

Pass: exit 1. The first three matches are `True`. The last is `False` (no
banner). The `[in#0 @ ...]` prefixes are visible, not swallowed as rich markup.

Do not fail this step on the exit number. On Windows it shows as the unsigned
`3199971767`. That is the known, parked issue in `CLAUDE.md` and the README's
Known limitations.

Fail: a message dominated by banner or stream metadata (the failure ADR 0004
fixed), or a `MarkupError` traceback.

### F5. Truncated real video

```powershell
Invoke-Clipper "$S\truncated.mp4"
```

Pass: exit 1 with `ffmpeg failed (exit` and a readable cause line. Record the
cause ffmpeg gave. A truncated MP4 with the `moov` atom cut off is expected to
fail the same way as F4.

### F6. Multi-audio file extracts the first audio stream

```powershell
Invoke-Clipper "$S\multi-audio.mp4" -V
```

Pass: exit 0, a results table, and `Saved:` with `multi-audio.json`.

Fail: an ffmpeg error from the WAV muxer. `-map 0:a:0?` was changed back to
`-map 0:a?` (ADR 0005).

### F7. ffmpeg missing from `PATH`

Use an empty cache, or a cache hit will skip ffmpeg and the step will pass for
the wrong reason. `uv` can live in the same folder as `ffmpeg` (WinGet does
this), so call it by absolute path.

```powershell
$SavedPath = $env:PATH
$env:CLIPPER_CACHE_DIR  = "$S\cache-f7"
$env:CLIPPER_OUTPUT_DIR = "$S\out-f7"
$uv = (Get-Command uv).Source
$ffDir = Split-Path (Get-Command ffmpeg).Source
$env:PATH = ($env:PATH -split ';' |
  Where-Object { $_ -and ($_.TrimEnd('\') -ne $ffDir.TrimEnd('\')) }) -join ';'
[bool](Get-Command ffmpeg -ErrorAction SilentlyContinue)   # must print False
cmd /c "`"$uv`" run clipper analyze `"$S\normal.mp4`" > `"$S\last.txt`" 2>&1"
"exit $LASTEXITCODE"; Get-Content "$S\last.txt"
```

Pass: `False` from the check, exit 1, and the message is
`Error: 'ffmpeg' not found on PATH. Install ffmpeg first.`. Nothing is created
in `$S\cache-f7`.

Then restore the session before continuing. The remaining steps need ffmpeg
back on `PATH` and the original cache and output folders:

```powershell
$env:PATH = $SavedPath
$env:CLIPPER_CACHE_DIR  = "$S\cache"
$env:CLIPPER_OUTPUT_DIR = "$S\out"
```

### F8. Path containing spaces

```powershell
Invoke-Clipper "$S\with space\normal clip.mp4" -V
```

Pass: exit 0 and `normal clip.json` in `$S\out`.

### F9. `-v` shows the full ffmpeg stderr, and no temp files leak

```powershell
Invoke-Clipper "$S\no-audio.mp4" -V
@(Get-ChildItem $env:TEMP -Directory -Filter "clipper-*" -ErrorAction SilentlyContinue).Count
```

Pass: the text contains an `INFO clipper.adapters.media.ffmpeg: ffmpeg stderr:`
line followed by the full ffmpeg output, then the `Error:` line. The count of
`clipper-*` folders in `%TEMP%` is `0`. Re-run after F4 and F5 as well, since
failures are where cleanup is easy to miss.

## Whisper

The first `base` run needs the model downloaded. Run W1 once before timing
anything. Timings depend on the machine, so compare runs on the same one.

### W1. Cold run

Starts from an empty transcript cache (the model is already downloaded).

```powershell
Remove-Item -Recurse -Force "$S\cache", "$S\out" -ErrorAction SilentlyContinue
$t = Measure-Command { Invoke-Clipper "$S\normal.mp4" -V | Out-Null }
$Code; $Text
"seconds: $($t.TotalSeconds)"
Copy-Item "$S\out\normal.json" "$S\normal-run1.json"
"cache files: " + @(Get-ChildItem "$S\cache").Count
```

Pass: exit 0. The log shows `Processing audio with duration`,
`Detected language` and `Detecting clips`, and does **not** show
`Transcript cache hit`. A table with at least one row, `Saved:`, and one file
in the cache. Record the seconds.

Fail: any `Transcription failed: ...` message. A TypeError about
`metadata_errors` means the `av` pin has slipped (ADR 0003).

### W2. Warm run

```powershell
$t = Measure-Command { Invoke-Clipper "$S\normal.mp4" -V | Out-Null }
$Code; $Text
"seconds: $($t.TotalSeconds)"
```

Pass: the log shows `Transcript cache hit: <name>.json`, there is no
`Processing audio` line, and the run is much faster than W1. Record the
seconds.

### W3. Changed file invalidates the cache

The cache key is the model namespace, file name, size and modified time.

```powershell
(Get-Item "$S\normal.mp4").LastWriteTime = Get-Date
Invoke-Clipper "$S\normal.mp4" -V
@(Get-ChildItem "$S\cache").Count
```

Pass: no cache-hit line, `Processing audio` appears again, and the cache holds
one more file than after W1.

### W4. Changed `CLIPPER_WHISPER_MODEL`

```powershell
$env:CLIPPER_WHISPER_MODEL = "tiny"
$t = Measure-Command { Invoke-Clipper "$S\normal.mp4" -V | Out-Null }
$Code; $Text                               # first tiny run
"seconds: $($t.TotalSeconds)"
Invoke-Clipper "$S\normal.mp4" -V          # second run
Remove-Item Env:CLIPPER_WHISPER_MODEL
Invoke-Clipper "$S\normal.mp4" -V          # back to base
```

Pass:

- The first `tiny` run is a cache miss (a different model has a different
  cache namespace) and, on a machine that has never used `tiny`, downloads the
  model. Record the time, which includes the download.
- The second `tiny` run is a cache hit.
- The `base` run is a cache hit too: switching models does not discard the
  other model's cache.

If `tiny` is already downloaded on your machine, the first run will not show a
download. That is expected.

Observe and record: on the reference run below, the download printed an
`hf_xet` fallback notice and a Hugging Face symlink warning on Windows. Both
are warnings from `huggingface_hub`, and the run still succeeded.

### W5. Invalid model name (observe and record)

```powershell
$env:CLIPPER_WHISPER_MODEL = "not-a-real-model"
Invoke-Clipper "$S\normal.mp4"
Remove-Item Env:CLIPPER_WHISPER_MODEL
```

Record the exit code and message. Pass if it exits 1 with an `Error:` line and
no traceback. The message text comes from faster-whisper, so it may change
between versions.

### W6. First run without network (observe and record)

Pick a model that is not downloaded yet. List what is:

```powershell
Get-ChildItem (Join-Path $env:USERPROFILE ".cache\huggingface\hub") -Directory |
  Select-Object -ExpandProperty Name
```

`HF_HUB_OFFLINE=1` simulates being offline. For the real case, disconnect the
network instead.

```powershell
$env:CLIPPER_WHISPER_MODEL = "small"   # must not be downloaded yet
$env:HF_HUB_OFFLINE = "1"
Invoke-Clipper "$S\normal.mp4"
Remove-Item Env:HF_HUB_OFFLINE, Env:CLIPPER_WHISPER_MODEL
```

Record the exit code and message. Pass if it exits 1 with an `Error:` line and
no traceback, and the model folder is not created.

### W7. Determinism

W1 and W3 are both cold runs of the same audio, so their saved JSON can be
compared. W2 cannot be used: it is a cache hit.

```powershell
(Get-FileHash "$S\normal-run1.json").Hash -eq (Get-FileHash "$S\out\normal.json").Hash
```

This compares W1's saved JSON with the current `normal.json`, so the last run
of `normal.mp4` must have used the `base` model. W4 ends on `base`, so running
W7 after it is fine. Pass: `True`.

### W8. Audio with no speech (observe and record)

```powershell
ffmpeg -hide_banner -loglevel error -y -f lavfi -i "color=c=black:s=320x240:d=10" `
  -f lavfi -i "sine=frequency=440:duration=10" -shortest -c:v libx264 -c:a aac "$S\tone.mp4"
Invoke-Clipper "$S\tone.mp4" -V
```

Record the exit code and what is printed. On the reference run, the VAD
filter removed all 10 seconds of audio, the run exited 0, and it printed an
empty table and saved a JSON file with no candidates. That is current
behaviour, not necessarily the intended behaviour. Do not fail the release on
this step. Note any change.

### W9. Word timestamps

Covers [ADR 0017](decisions/0017-word-timestamps-and-styled-captions.md).
W1's saved JSON holds every segment's words. This checks them against the
segment they belong to.

```powershell
$check = @'
import json, re, sys
segs = json.load(open(sys.argv[1], encoding="utf-8"))["transcript"]["segments"]
norm = lambda t: re.sub(r"\s+", " ", t).strip()
words = [w for s in segs for w in s["words"]]
print("segments:", len(segs), "words:", len(words))
print("segments without words:", sum(not s["words"] for s in segs))
print("zero-length words:", sum(w["end_seconds"] == w["start_seconds"] for w in words))
outside = sum(w["start_seconds"] < s["start_seconds"] or w["end_seconds"] > s["end_seconds"]
              for s in segs for w in s["words"])
print("words outside their segment:", outside)
for s in segs:
    joined = norm(" ".join(w["text"] for w in s["words"]))
    if joined != norm(s["text"]):
        print("differs:", repr(norm(s["text"])), "vs", repr(joined))
'@
Set-Content "$S\check_words.py" $check -Encoding utf8
uv run python "$S\check_words.py" "$S\normal-run1.json"
```

Pass: no segment without words, no word outside its segment (the model
rejects that, so a load failure is also a fail), and no `differs:` lines.
Record the counts. A `differs:` line means a word was split or lost: look at
how faster-whisper spaced the raw words (`_clean_words` in
`adapters/transcription/faster_whisper.py`).

The first `analyze` of a video after upgrading from v0.3 re-transcribes it:
transcripts cached without word timestamps are under a different cache key.

## Rendering

Covers `clipper render` ([ADR 0007](decisions/0007-cut-clips-by-re-encoding.md),
[0008](decisions/0008-clip-output-location-and-naming.md),
[0009](decisions/0009-render-reads-saved-json-and-probes-media.md),
[0011](decisions/0011-render-command-shape-and-failure-policy.md),
[0019](decisions/0019-a-folder-per-video.md), and
[0018](decisions/0018-suggested-titles-via-ollama.md) in R19). Clips go to a
folder per video,
`<clips folder>\<stem>`, with a `manifest.json`. It is
independent of the ffmpeg and Whisper steps above, with its own setup, scratch
folder and helpers. Run R0 first in a fresh PowerShell window, and keep that
window for the rest of the section. Every later step uses the variables and
helpers R0 defines.

Where a step says "record", write down what you saw. The expected result is
described, not quoted, because ffmpeg and ffprobe wording belongs to the
installed build.

Fixtures marked **constructed** are made by the commands in R0 and are not
real analysis output. They exist so a failure path can be reached on purpose.

Two things to know when you paste commands and record the run:

- Paste one block at a time. A block whose last line has no trailing newline
  can merge with the first line of the next paste and run as one broken
  command (a `Test-Path` on a path that never exists prints `False` for the
  wrong reason).
- If you use `Start-Transcript`, it does not capture native command output
  printed straight to the console, such as the `analyze` table or a bare
  `ffprobe` line. Pipe it (`... | ForEach-Object { $_ }`) or assign it to a
  variable and print that, as R3 does.

### R0. Setup

You need a **long speech video** with an audio track. The reference run used a
5:14 clip, which gave 5 candidates. Steps R4 and R5 need at least 3 candidates
and R1 to R2 are most useful with 5.

```powershell
$Src  = "path\to\speech.mp4"                               # your long speech video
$Name = Split-Path $Src -Leaf
$Stem = [IO.Path]::GetFileNameWithoutExtension($Src)
$S    = Join-Path $env:TEMP "manual-test-clipper-render"
New-Item -ItemType Directory -Force $S, "$S\short", "$S\other" | Out-Null

$env:COLUMNS            = "500"
$env:CLIPPER_CACHE_DIR  = "$S\cache"
$env:CLIPPER_OUTPUT_DIR = "$S\out"
$env:CLIPPER_CLIPS_DIR  = "$S\clips"
$Json = "$S\out\$Stem.json"

# Helper: runs `clipper render`, keeps the exit code and text, prints both.
function Invoke-Render {
  param([string]$Video, [string]$ResultJson, [int[]]$Rank = @())
  $flags = ($Rank | ForEach-Object { "--rank $_" }) -join " "
  cmd /c "uv run clipper render `"$Video`" `"$ResultJson`" $flags > `"$S\last.txt`" 2>&1"
  $script:Code = $LASTEXITCODE
  $script:Text = Get-Content "$S\last.txt" -Raw -Encoding UTF8
  "exit $Code"
  $Text
}

# Helpers: empty the clips folder, and list it.
function Reset-Clips { Remove-Item -Recurse -Force $env:CLIPPER_CLIPS_DIR -ErrorAction SilentlyContinue }
function Show-Clips {
  if (Test-Path $env:CLIPPER_CLIPS_DIR) {
    Get-ChildItem $env:CLIPPER_CLIPS_DIR -Recurse -File |
      Select-Object @{n="Folder";e={$_.Directory.Name}}, Name, Length | Format-Table -AutoSize
  } else { "(clips folder does not exist)" }
}

# Helper (constructed fixtures): writes an analysis result JSON with one
# candidate per "start-end" span, an empty transcript and a fixed score.
function New-Result {
  param([string]$Path, [string]$Source, [string[]]$Spans = @())
  $items = foreach ($sp in $Spans) {
    $a, $b = $sp -split "-"
    '{"start_seconds": ' + $a + ', "end_seconds": ' + $b + ', "score": 0.5, "reasons": ["constructed"]}'
  }
  $json = '{"source": "' + $Source + '", "transcript": {"segments": []}, "candidates": [' + ($items -join ",") + ']}'
  Set-Content -Path $Path -Value $json -Encoding ascii
}

# Real analysis of the source. Produces $Json.
uv run clipper analyze "$Src"
"exit $LASTEXITCODE"

# Constructed fixtures.
# testsrc.mp4: 40 s of lavfi test video. A large number drawn on the picture is
# the source time in whole seconds. x264's default keyframe interval is 250
# frames, which is 10 s at 25 fps.
ffmpeg -hide_banner -loglevel error -y -f lavfi -i "testsrc=size=640x360:rate=25:duration=40" `
  -f lavfi -i "sine=frequency=440:duration=40" -shortest `
  -c:v libx264 -pix_fmt yuv420p -c:a aac "$S\testsrc.mp4"
New-Result "$S\testsrc.json"     "testsrc.mp4" "12.5-20"
New-Result "$S\testsrc-two.json" "testsrc.mp4" "12.5-20", "25-35"
# other\testsrc.mp4: a different 40 s video with the same file name (R8).
ffmpeg -hide_banner -loglevel error -y -f lavfi -i "smptebars=size=640x360:rate=25:duration=40" `
  -f lavfi -i "sine=frequency=880:duration=40" -shortest `
  -c:v libx264 -pix_fmt yuv420p -c:a aac "$S\other\testsrc.mp4"
# short\<same name as the source>: 1 s of the source, so candidates overrun it (R9).
ffmpeg -hide_banner -loglevel error -y -t 1 -i "$Src" `
  -c:v libx264 -pix_fmt yuv420p -c:a aac "$S\short\$Name"
# audio-only.m4a and its JSON (R10).
ffmpeg -hide_banner -loglevel error -y -f lavfi -i "sine=frequency=440:duration=10" `
  -c:a aac "$S\audio-only.m4a"
New-Result "$S\audio-only.json"  "audio-only.m4a" "1-5"
# A result with no candidates (R11).
New-Result "$S\empty.json"       $Name

# What the real analysis found. These are the candidates R1 to R6 work from.
$r = Get-Content $Json -Raw -Encoding UTF8 | ConvertFrom-Json
"candidates: " + $r.candidates.Count
$r.candidates | ForEach-Object { "{0,8:N1} - {1,8:N1}  score {2}" -f $_.start_seconds, $_.end_seconds, $_.score }
```

Pass: `analyze` exits 0 and prints `Saved:` with `$Stem.json`. Every `ffmpeg`
command above prints nothing. The candidate list prints. Record the count
and the spans.

### R1. Render every candidate (the first real run)

```powershell
Reset-Clips
$t = Measure-Command { Invoke-Render $Src $Json | Out-Null }
$Code; $Text
"seconds: $($t.TotalSeconds)"
Show-Clips
```

Pass:

- Exit 0.
- One `Rendered #N H:MM:SS-H:MM:SS <path>` line per candidate, in rank order,
  where `H:MM:SS-H:MM:SS` matches the candidate's span from R0.
- A line `Rendered X of X clips to <clips folder>\<stem>`, with `X` equal to
  the candidate count, then `Manifest: <clips folder>\<stem>\manifest.json`.
- The clips folder holds one folder, `<stem>` (ADR 0019), with `X` files named
  `<stem>_HHMMSS-HHMMSS.mp4` and `manifest.json`. In each name the start is the
  candidate's start rounded **down** and the end is its end rounded **up** to
  whole seconds (ADR 0008). R18 checks the manifest.

The console line prints both ends rounded **down**, while the file name rounds
the end **up**. A candidate ending at 73.8 s prints `0:01:13` but is named
`...-000114.mp4`, so the two can differ by one second. That is not a failure.

Record the seconds, which is the first render time measured for this tool
(ADR 0007 notes none exists), and the file sizes.

### R2. Rendered clips are H.264, AAC, yuv420p, and the right length

Run after R1, without resetting the clips folder.

```powershell
$cands = $r.candidates | Sort-Object start_seconds
$files = Get-ChildItem "$env:CLIPPER_CLIPS_DIR\$Stem" -Filter "*.mp4" | Sort-Object Name
for ($i = 0; $i -lt $files.Count; $i++) {
  $f = $files[$i].FullName
  $want = $cands[$i].end_seconds - $cands[$i].start_seconds
  $got  = [double](ffprobe -v error -show_entries format=duration -of csv=p=0 $f)
  $v = ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,pix_fmt -of csv=p=0 $f
  $a = ffprobe -v error -select_streams a:0 -show_entries stream=codec_name -of csv=p=0 $f
  $kinds = (ffprobe -v error -show_entries stream=codec_type -of csv=p=0 $f) -join ","
  "{0}`n  video: {1}  audio: {2}  streams: {3}`n  requested {4:N3} s, actual {5:N3} s, diff {6:N3} s" -f `
    $files[$i].Name, $v, $a, $kinds, $want, $got, ($got - $want)
}
```

Pass:

- The video line contains `h264` and `yuv420p`, the audio line is `aac`, and
  the streams are `video,audio`.
- Every file's actual duration is close to the requested one. Record the
  largest difference. The one earlier by-hand measurement was 10.01 s for a
  requested 10.0 s. A difference of a second or more points at keyframe
  snapping, which the re-encode in ADR 0007 is meant to avoid.

If the source has more than one audio stream, `streams` will show only one
audio stream anyway: `-map 0:a:0?` keeps the first (ADR 0007).

### R3. A clip starts and ends where it was asked to

The only check of the intent in ADR 0007 that cuts are frame-accurate and not
snapped to keyframes. It uses the constructed `testsrc.mp4`, whose picture shows
the source time in whole seconds, and the constructed `testsrc.json`, with one
candidate from 12.5 s to 20 s.

```powershell
Reset-Clips
Invoke-Render "$S\testsrc.mp4" "$S\testsrc.json"
$Clip = "$env:CLIPPER_CLIPS_DIR\testsrc\testsrc_000012-000020.mp4"
ffmpeg -hide_banner -loglevel error -y -i $Clip -frames:v 1 "$S\first.png"
ffmpeg -hide_banner -loglevel error -y -sseof -0.1 -i $Clip -frames:v 1 "$S\last.png"
$d = ffprobe -v error -show_entries format=duration -of csv=p=0 $Clip
"duration: $d"
Invoke-Item "$S\first.png"; Invoke-Item "$S\last.png"
```

Pass:

- Exit 0 and the clip is named `testsrc_000012-000020.mp4`.
- The duration is close to 7.5. Record it.
- `first.png` shows **12**: the source second the cut started in.
- `last.png` shows **19**: the last frames are just before 20 s.

Fail: `first.png` shows **10**. The cut snapped back to the keyframe at 10 s,
which means the cut stopped being a re-encode or `-ss` moved (ADR 0007). The
picture only resolves whole seconds, so a cut off by less than a second will
not show here. R2's duration check covers the length.

### R4. `--rank` selects, sorts and de-duplicates

Needs at least 3 candidates.

```powershell
Reset-Clips
Invoke-Render $Src $Json -Rank 3,1,1
Show-Clips
```

Pass: exit 0. Exactly two `Rendered` lines, `#1` first and then `#3`, however
the flags were given. Then `Rendered 2 of 2 clips` and a `Manifest:` line. The
video's folder holds two clips, the ones for candidates 1 and 3 from R0, and
`manifest.json`.

### R5. Re-running is idempotent, and old clips are left alone

Run after R4, without resetting the clips folder.

```powershell
$before = Get-ChildItem $env:CLIPPER_CLIPS_DIR -Recurse -Filter "*.mp4" | Select-Object -ExpandProperty Name
Invoke-Render $Src $Json -Rank 1,3 | Out-Null
$Code
$after = Get-ChildItem $env:CLIPPER_CLIPS_DIR -Recurse -Filter "*.mp4" | Select-Object -ExpandProperty Name
"same names: " + (-not (Compare-Object $before $after))
Invoke-Render $Src $Json -Rank 2 | Out-Null
$Code
@(Get-ChildItem $env:CLIPPER_CLIPS_DIR -Recurse -Filter "*.mp4").Count
```

Pass: exit 0 both times. `same names: True`, with no numbered or suffixed
copies. After rendering rank 2 the folder holds 3 clips, so the clips from R4
were not deleted (ADR 0008: "nothing deletes old files").

### R6. Out-of-range ranks fail before cutting

```powershell
Reset-Clips
Invoke-Render $Src $Json -Rank 99
Invoke-Render $Src $Json -Rank 0
Invoke-Render $Src $Json -Rank 1,99
Test-Path $env:CLIPPER_CLIPS_DIR
```

Pass: all three exit 1. The text starts with `Error: Rank 99 out of range: the
result has N candidates` (`Rank 0 ...` for the second run), where `N` is the
count from R0. The third run renders nothing, even though rank 1 is valid, and
`Test-Path` prints `False`: nothing was written and the clips folder was not
even created. It is a rendering error and not a Typer usage error (ADR 0011).

### R7. Result saved for a differently named video

```powershell
Reset-Clips
Invoke-Render "$S\testsrc.mp4" $Json
Test-Path $env:CLIPPER_CLIPS_DIR
```

Pass: exit 1 with `Analysis result is for '<your source name>' but the video is
'testsrc.mp4'`, and `Test-Path` prints `False`.

### R8. Same file name, different video (observe and record)

This is the known weak spot of the name check (ADR 0009): `other\testsrc.mp4`
is a different video with the same name as the one `testsrc.json` was saved
for.

```powershell
Reset-Clips
Invoke-Render "$S\other\testsrc.mp4" "$S\testsrc.json"
Show-Clips
```

Pass: exit 0 and one clip, with no error. That is current, documented
behaviour: the wrong picture is cut without a warning. Record any change. If
this step starts failing, a fingerprint was added and ADR 0009 needs its
"Revisit when" updated.

### R9. A candidate past the end of the video

```powershell
Reset-Clips
Invoke-Render "$S\short\$Name" $Json
Test-Path $env:CLIPPER_CLIPS_DIR
```

Pass: exit 1 with `Candidate H:MM:SS-H:MM:SS ends after the end of the video
(` followed by a duration near 1 s. The span named is one of the candidates
from R0. `Test-Path` prints `False`. This is also the first check of the real
`ffprobe` duration against `MediaInfo`.

### R10. File with no video stream

Constructed: an audio-only file, and a result whose `source` matches it.

```powershell
Reset-Clips
Invoke-Render "$S\audio-only.m4a" "$S\audio-only.json"
Test-Path $env:CLIPPER_CLIPS_DIR
```

Pass: exit 1 with `The video file has no video stream`, and `Test-Path`
prints `False`. This is the first check of the real `ffprobe` stream and
`attached_pic` JSON against the code in `ffprobe.py`.

### R11. Bad, missing and empty result files

```powershell
Reset-Clips
Set-Content "$S\bad.json" "this is not json" -Encoding ascii
Set-Content "$S\wrong-shape.json" '{"a": 1}' -Encoding ascii
Invoke-Render $Src "$S\bad.json"
Invoke-Render $Src "$S\wrong-shape.json"
Invoke-Render $Src "$S\does-not-exist.json"
Invoke-Render $Src "$S\empty.json"
Test-Path $env:CLIPPER_CLIPS_DIR
```

Pass: all four exit 1, with no traceback. In order, the messages are:
`Invalid JSON in analysis result <path>: ...`, `<path> is not a valid analysis
result: source: ...`, `Analysis result not found: <path>`, and `The analysis
result has no candidates to render`. `Test-Path` prints `False`.

### R12. Video file missing

```powershell
Invoke-Render "$S\gone\$Name" $Json
```

Pass: exit 1 with `Input file not found: <path>`. The name check passes first,
so this message comes from the probe.

### R13. `ffprobe` missing from `PATH`

Modelled on F7. `ffprobe` normally lives in the same folder as `ffmpeg`, so
removing that folder removes both. Call `uv` by absolute path.

```powershell
Reset-Clips
$SavedPath = $env:PATH
$uv = (Get-Command uv).Source
$ffDir = Split-Path (Get-Command ffprobe).Source
$env:PATH = ($env:PATH -split ';' |
  Where-Object { $_ -and ($_.TrimEnd('\') -ne $ffDir.TrimEnd('\')) }) -join ';'
[bool](Get-Command ffprobe -ErrorAction SilentlyContinue)   # must print False
cmd /c "`"$uv`" run clipper render `"$Src`" `"$Json`" > `"$S\last.txt`" 2>&1"
"exit $LASTEXITCODE"; Get-Content "$S\last.txt"
Test-Path $env:CLIPPER_CLIPS_DIR
$env:PATH = $SavedPath
```

Pass: `False` from the check, exit 1, the message
`Error: 'ffprobe' not found on PATH. Install ffmpeg first.`, and `Test-Path`
prints `False`. `PATH` is restored by the last line.

### R14. One clip fails, the rest still render (observe and record)

Constructed failure: before rendering, put a **folder** where the second clip's
file should go, so ffmpeg cannot write it. `testsrc-two.json` has candidates
12.5 to 20 s and 25 to 35 s.

```powershell
Reset-Clips
New-Item -ItemType Directory -Force "$env:CLIPPER_CLIPS_DIR\testsrc\testsrc_000025-000035.mp4" | Out-Null
Invoke-Render "$S\testsrc.mp4" "$S\testsrc-two.json"
Show-Clips
Test-Path "$env:CLIPPER_CLIPS_DIR\testsrc\testsrc_000025-000035.mp4" -PathType Container
```

Pass:

- A `Rendered #1 ...` line for `testsrc_000012-000020.mp4`, and that file exists
  and is not empty.
- A `Failed: #2 0:00:25-0:00:35: ffmpeg failed (exit ...): ...` line. Record the
  cause ffmpeg gave. I have not seen it.
- `Rendered 1 of 2 clips to <clips folder>\testsrc`, then a `Manifest:` line,
  and the exit code is 1 (ADR 0011).
- No traceback. The renderer tries to delete the failed output and cannot
  delete a folder, which must be logged and not raised, so the last command
  prints `True`.

The exit number may show as an unsigned 32-bit value on Windows. That is the
parked issue in `CLAUDE.md`. Do not fail the step on it. If ffmpeg unexpectedly
succeeds here, record that and the contents of the folder.

### R15. `--vertical` fits the frame into 9:16 at 1080x1920

Covers [ADR 0016](decisions/0016-vertical-centre-crop.md). `Invoke-Render` in R0
takes ranks only, so this step defines a helper that passes any flags.

```powershell
# Helper: like Invoke-Render, with the flags passed as one string.
function Invoke-RenderWith {
  param([string]$Video, [string]$ResultJson, [string]$Flags)
  cmd /c "uv run clipper render `"$Video`" `"$ResultJson`" $Flags > `"$S\last.txt`" 2>&1"
  $script:Code = $LASTEXITCODE
  $script:Text = Get-Content "$S\last.txt" -Raw -Encoding UTF8
  "exit $Code"
  $Text
}
# Helper: what ffprobe says about a clip's streams and length.
function Show-Streams([string]$Clip) {
  ffprobe -v error -show_entries "stream=codec_type,codec_name,width,height,sample_aspect_ratio,pix_fmt:format=duration" -of compact=p=0 "$Clip"
}

Reset-Clips
"source: " + (ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "$Src")
Invoke-RenderWith $Src $Json "--vertical --rank 1"
$clip = (Get-ChildItem $env:CLIPPER_CLIPS_DIR -Recurse -Filter "*.mp4" | Select-Object -First 1).FullName
Show-Streams $clip
ffmpeg -hide_banner -loglevel error -y -ss 5 -i $clip -frames:v 1 "$S\vertical.png"
Invoke-Item "$S\vertical.png"
```

Pass:

- Exit 0, and the `Rendered #1 ...` line ends with ` (vertical)`. The file name
  is the same as R1's for rank 1.
- The video stream is `h264`, `width=1080`, `height=1920`,
  `sample_aspect_ratio=1:1`, `yuv420p`; the audio stream is `aac`; the duration
  is within R2's tolerance of the requested length.
- In `vertical.png`, the whole source frame is visible, full width, centred,
  and not squashed or stretched, on a blurred copy of itself that fills the
  rest of the frame.

A source with black bars built into its frame keeps them, inside the fitted
picture and blurred in the fill.

### R16. `--vertical --captions`: captions drawn on the vertical frame

```powershell
Reset-Clips
Invoke-RenderWith $Src $Json "--vertical --captions --rank 1"
$clip = (Get-ChildItem $env:CLIPPER_CLIPS_DIR -Recurse -Filter "*.mp4" | Select-Object -First 1).FullName
Show-Streams $clip
ffmpeg -hide_banner -loglevel error -y -ss 5 -i $clip -frames:v 1 "$S\vertical-captions.png"
Invoke-Item "$S\vertical-captions.png"
```

Pass: exit 0, the line ends with ` (vertical, captions)`, the streams are as in
R15, and the frame shows a caption of one to three words in the blurred fill
below the picture, bold white with at most one word in yellow, on one line
(ADR 0017). A frame in a pause between chunks shows no caption; rerun the
`ffmpeg` line at another time if 5 s falls in one. The Captions section checks
the words and their timing.

### R17. Portrait and narrow sources (constructed)

Two constructed sources: 720x1280, which is already 9:16, so it fills the
frame, and 600x1280, which is narrower than 9:16, so it fits by height with
the blurred fill at the sides. Each has one candidate, 2 to 8 s.

```powershell
foreach ($p in @(@{ n = "portrait"; s = "720x1280" }, @{ n = "narrow"; s = "600x1280" })) {
  ffmpeg -hide_banner -loglevel error -y -f lavfi -i "testsrc=size=$($p.s):rate=25:duration=10" `
    -f lavfi -i "sine=frequency=440:duration=10" -shortest `
    -c:v libx264 -pix_fmt yuv420p -c:a aac "$S\$($p.n).mp4"
  New-Result "$S\$($p.n).json" "$($p.n).mp4" "2-8"
  Reset-Clips
  Invoke-RenderWith "$S\$($p.n).mp4" "$S\$($p.n).json" "--vertical"
  $clip = (Get-ChildItem $env:CLIPPER_CLIPS_DIR -Recurse -Filter "*.mp4" | Select-Object -First 1).FullName
  Show-Streams $clip
  ffmpeg -hide_banner -loglevel error -y -ss 3 -i $clip -frames:v 1 "$S\$($p.n).png"
}
Invoke-Item "$S\narrow.png"
```

Pass: both exit 0 with streams as in R15 and a duration of 6 s. `narrow.png`
shows the number `5` (2 s into the source, plus 3 s into the clip), the test
picture at full height and not visibly stretched, and blurred strips at the
sides.

### R18. The folder per video and its manifest

Covers [ADR 0019](decisions/0019-a-folder-per-video.md). Uses `Invoke-RenderWith`
from R15.

```powershell
# a) Every candidate, vertical with captions.
Reset-Clips
Invoke-RenderWith $Src $Json "--vertical --captions"
$Dir = "$env:CLIPPER_CLIPS_DIR\$Stem"
$m = Get-Content "$Dir\manifest.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$m | Select-Object source, analysis, detector, vertical, captions | Format-List
$m.clips | Format-Table rank, start_seconds, end_seconds, score, file, captions, error -AutoSize
$listed = @($m.clips | ForEach-Object { $_.file })
$onDisk = @(Get-ChildItem $Dir -Filter "*.mp4" | ForEach-Object { $_.Name })
"listed files match the folder: " + (-not (Compare-Object $listed $onDisk))

# b) A --rank run replaces the manifest.
Invoke-RenderWith $Src $Json "--rank 2" | Out-Null
"b) exit $Code"
$m = Get-Content "$Dir\manifest.json" -Raw -Encoding UTF8 | ConvertFrom-Json
# ForEach-Object, not $m.clips.rank: an array has its own Rank property, 1.
"ranks: " + (($m.clips | ForEach-Object { $_.rank }) -join ",")
"vertical: $($m.vertical), captions: $($m.captions)"
"clips in the folder: " + @(Get-ChildItem $Dir -Filter "*.mp4").Count

# c) A failed clip is listed with its error (the constructed failure from R14).
Reset-Clips
New-Item -ItemType Directory -Force "$env:CLIPPER_CLIPS_DIR\testsrc\testsrc_000025-000035.mp4" | Out-Null
Invoke-Render "$S\testsrc.mp4" "$S\testsrc-two.json"
$tm = "$env:CLIPPER_CLIPS_DIR\testsrc\manifest.json"
(Get-Content $tm -Raw -Encoding UTF8 | ConvertFrom-Json).clips | Format-List rank, file, error

# d) A failure before cutting leaves the manifest as it was.
$h = (Get-FileHash $tm).Hash
Invoke-Render "$S\testsrc.mp4" "$S\testsrc-two.json" -Rank 9 | Out-Null
"d) exit $Code, manifest unchanged: " + ((Get-FileHash $tm).Hash -eq $h)
```

Pass:

- a) Exit 0 and a last line `Manifest: <clips folder>\<stem>\manifest.json`.
  `source` is the video's file name, `analysis` is `$Json` as passed,
  `detector` is `heuristic`, and `vertical` and `captions` are `True`. One row
  per candidate in rank order, with the spans and scores from R0, a `file`, no
  `error`, and `captions` `words` (empty for a clip that printed a `has no
  speech in range` note). `listed files match the folder: True`.
- b) Exit 0, `ranks: 2`, `vertical: False, captions: False`, and
  `clips in the folder:` still counts every clip from a).
- c) Exit 1. Rank 1 has a `file` and no `error`. Rank 2 has no `file`, and its
  `error` starts `ffmpeg failed (exit` and holds the same cause as the
  `Failed: #2` line.
- d) `exit 1, manifest unchanged: True`.

### R19. `--titles`: a title and hook per clip from the real Ollama server

Covers [ADR 0018](decisions/0018-suggested-titles-via-ollama.md). The title
tests use a fake writer and a fake generator, so nothing in CI sends the title
request to a real model. This step checks that Ollama accepts it, what the
titles look like, how long they take, and whether two runs agree.

It needs Ollama running with the model `CLIPPER_LLM_MODEL` names (default
`qwen3.5:9b`) pulled; L0 has the setup. Never copy a title into the docs that
did not come from a run.

```powershell
# Helper: like Invoke-Render, with the flags passed as one string and extra
# CLIPPER_ variables set for this one command, then put back.
function Invoke-Titles {
  param([string]$Video, [string]$ResultJson, [string]$Flags, [hashtable]$Extra = @{})
  $old = @{}
  foreach ($k in $Extra.Keys) {
    $old[$k] = [Environment]::GetEnvironmentVariable($k, "Process")
    [Environment]::SetEnvironmentVariable($k, $Extra[$k], "Process")
  }
  try {
    $t = Measure-Command {
      cmd /c "uv run clipper render `"$Video`" `"$ResultJson`" $Flags > `"$S\last.txt`" 2>&1"
    }
    $script:Code = $LASTEXITCODE
  } finally {
    foreach ($k in $Extra.Keys) {
      [Environment]::SetEnvironmentVariable($k, $old[$k], "Process")
    }
  }
  $script:Seconds = [Math]::Round($t.TotalSeconds, 1)
  $script:Text = Get-Content "$S\last.txt" -Raw -Encoding UTF8
  "exit $Code, $Seconds s"
  $Text
}
# Helper: the titles and hooks in a video's manifest, with their lengths.
function Show-Titles([string]$Manifest) {
  (Get-Content $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json).clips |
    ForEach-Object { [pscustomobject]@{
      rank = $_.rank; title = $_.title; tlen = "$($_.title)".Length
      hook = $_.hook; hlen = "$($_.hook)".Length; error = $_.error } } |
    Format-List
}
$Model = if ($env:CLIPPER_LLM_MODEL) { $env:CLIPPER_LLM_MODEL } else { "qwen3.5:9b" }
$Dir = "$env:CLIPPER_CLIPS_DIR\$Stem"

# a) Every candidate, vertical with captions and titles. Unload the model
# first, so the time includes loading it.
ollama stop $Model
Reset-Clips
Invoke-Titles $Src $Json "--vertical --captions --titles"
$m = Get-Content "$Dir\manifest.json" -Raw -Encoding UTF8 | ConvertFrom-Json
"titles: $($m.titles)"
Show-Titles "$Dir\manifest.json"
Copy-Item "$Dir\manifest.json" "$S\titles-1.json"

# b) The same again, for repeatability (ADR 0015). Titles only, so it is quick.
Invoke-Titles $Src $Json "--titles" | Out-Null
"b) exit $Code, $Seconds s"
$one = (Get-Content "$S\titles-1.json" -Raw -Encoding UTF8 | ConvertFrom-Json).clips |
  ForEach-Object { "$($_.rank)|$($_.title)|$($_.hook)" }
$two = (Get-Content "$Dir\manifest.json" -Raw -Encoding UTF8 | ConvertFrom-Json).clips |
  ForEach-Object { "$($_.rank)|$($_.title)|$($_.hook)" }
"same titles and hooks: " + (-not (Compare-Object $one $two))
Compare-Object $one $two

# c) Model not pulled. The tag is deliberately one that does not exist.
Reset-Clips
Invoke-Titles $Src $Json "--titles" @{ CLIPPER_LLM_MODEL = "no-such-model-clipper:latest" }
"clips in the folder: " + @(Get-ChildItem $Dir -Filter "*.mp4" -ErrorAction SilentlyContinue).Count
Show-Titles "$Dir\manifest.json"

# d) Nothing listening on the URL.
Invoke-Titles $Src $Json "--titles --rank 1" @{ CLIPPER_LLM_URL = "http://127.0.0.1:1" }

# e) A v0.3-style analysis: the same result with every segment's words removed.
$v3 = Get-Content $Json -Raw -Encoding UTF8 | ConvertFrom-Json
$v3.transcript.segments | ForEach-Object { $_.words = @() }
[IO.File]::WriteAllText("$S\out\$Stem-v03.json", ($v3 | ConvertTo-Json -Depth 10),
  (New-Object System.Text.UTF8Encoding $false))
Reset-Clips
Invoke-Titles $Src "$S\out\$Stem-v03.json" "--titles --rank 1"
Show-Titles "$Dir\manifest.json"

# f) No speech in range (constructed: testsrc.json has an empty transcript).
Reset-Clips
Invoke-Titles "$S\testsrc.mp4" "$S\testsrc.json" "--titles"
Show-Titles "$env:CLIPPER_CLIPS_DIR\testsrc\manifest.json"
```

Pass:

- a) Exit 0. Each `Rendered #N ...` line ends with
  ` (vertical, captions, titles)` and is followed by `  Title: ...` and
  `  Hook: ...` lines. `titles: True`. Every clip has a non-empty `title` and
  `hook` on one line, about what is said in it, with no hashtags or emoji, and
  no `error`. Titles have at most 8 words and hooks at most 15, the prompt's
  limits; the code does not check them, so record any over. Record the time,
  each title and hook as printed, their lengths, any swear word, and any that
  changes what the speaker meant (ADR 0018's title prompt amendment).
- b) Exit 0. Record whether `same titles and hooks` is `True`, and any rows
  `Compare-Object` lists. A difference is recorded, not a failure (ADR 0015).
- c) Exit 1. Every clip has a `Failed: #N ...` line with Ollama's HTTP 404
  message and the `ollama pull` hint, `clips in the folder: 0`, and every
  manifest entry has that `error` and no title.
- d) Exit 1. `Failed: #1 ...: Cannot reach Ollama at http://127.0.0.1:1: ...
  Is it running?`. Record the time.
- e) Exit 0, with a title and hook for rank 1. Rank 1's title may differ from
  a), since the text sent may differ (whole segments overlapping the clip,
  not only the words inside it).
- f) Exit 0, `Note: #1 0:00:12-0:00:20 has no speech in range; no title`, no
  `Title:` line, and the manifest's clip has a `file` and an empty `title` and
  `hook`.

The empty `CLIPPER_LLM_MODEL` error (`Error: Set CLIPPER_LLM_MODEL ... to use
--titles`, before cutting) is not checked here: Windows does not keep an empty
environment variable (see L2). The CLI unit tests cover it.

### R19a. Titles with the video's info

Covers the title context amendment to
[ADR 0018](decisions/0018-suggested-titles-via-ollama.md): with a
`<id>.info.json` beside the video (written by `fetch`, step D10a), the title
prompt gets the channel and the video's title. Use a fetched Twitch VOD that
has its info file and an analysis JSON, and R19's helpers.

```powershell
$Vod  = "data\videos\<a fetched Twitch VOD>.mp4"
$Json = "data\output\<the same id>.json"
$Info = $Vod -replace '\.mp4$', '.info.json'
Get-Content $Info -Encoding UTF8
Invoke-Titles $Vod $Json "--titles --rank 1 --rank 2 --rank 3" @{ CLIPPER_CLIPS_DIR = "$S\clips-r19a-with" }
Rename-Item $Info "$Info.off"
try {
  Invoke-Titles $Vod $Json "--titles --rank 1 --rank 2 --rank 3" @{ CLIPPER_CLIPS_DIR = "$S\clips-r19a-without" }
} finally { Rename-Item "$Info.off" $Info }
```

Pass: the first run prints `Title context: <the channel>` before the first
clip, the second does not, and both exit 0 with a title per clip. Record the
three titles from each run; with the info, some name the streamer (often by
the login, such as "Stable Ronaldo"). The info file is back in place
afterwards.

### R19b. Titles with `--about`

Covers the `--about` amendment to
[ADR 0018](decisions/0018-suggested-titles-via-ollama.md), with R19a's
video and helpers.

```powershell
Invoke-Titles $Vod $Json "--titles --rank 1 --rank 2 --rank 3 --about `"<the streamer's name> (<login>)`"" @{ CLIPPER_CLIPS_DIR = "$S\clips-r19b" }
Invoke-Titles $Vod $Json "--rank 1 --about `"<the name>`"" @{ CLIPPER_CLIPS_DIR = "$S\clips-r19b-off" }
```

Pass: the first run exits 0 with a title per clip, and most of them use the
name you gave rather than the login. To check pronouns, run the first line
again with `--about "<the name> (<their pronouns>)"`: no title or hook uses
other pronouns for them. Without pronouns, he or she may still appear
(ADR 0018, pronouns amendment). The second exits 1 with `Error: --about
is for titles; use it with titles on` and writes no folder. Record the three
titles next to R19a's.

### R19c. Titles with a channel's saved about text

Covers the saved about amendment to
[ADR 0018](decisions/0018-suggested-titles-via-ollama.md), with R19a's
video and helpers. `$Vod` must be on a channel listed in
`data\channels.txt`.

```powershell
Invoke-Titles $Vod $Json "--titles --rank 1 --rank 2 --rank 3" @{ CLIPPER_CLIPS_DIR = "$S\clips-r19c" }
Invoke-Titles $Vod $Json "--titles --rank 1 --about `"Someone else`"" @{ CLIPPER_CLIPS_DIR = "$S\clips-r19c-about" }
"stableronaldo |" | Set-Content "$S\bad-channels.txt" -Encoding UTF8
Invoke-Titles $Vod $Json "--titles --rank 1" @{ CLIPPER_CLIPS_DIR = "$S\clips-r19c-bad"; CLIPPER_CHANNELS_FILE = "$S\bad-channels.txt" }
```

Pass: the first run prints `About (saved for <channel>): <the file's
text>` after `Title context:`, and its titles use that name. The second
prints no `saved for` line. The third exits 1 with `Error: ...bad-channels.txt:
Line 1: write it as <channel> | <about text>` and writes no folder. Record
the first run's titles. Since the emoji amendment, each title ends with one
to three emoji, shown as written in the console and in the review page's
title box.

### R20. `--vertical --style face`: a crop that follows the face

Covers the face style ([ADR 0025](decisions/0025-face-crop-style-and-picking-styles.md)).
The tests fake the face tracker and the renderer's ffmpeg call, so nothing
in CI runs the real crop. Use a podcast or webcam clip and an IRL clip with
several people, each with an analysis JSON, and R19's `Invoke-Titles`
helper.

```powershell
$Pod = "data\videos\<a podcast or webcam video>.mp4"; $PodJson = "data\output\<its id>.json"
$Irl = "data\videos\<an IRL stream>.mp4";            $IrlJson = "data\output\<its id>.json"
Invoke-Titles $Pod $PodJson "--rank 1 --vertical --style face --captions" @{ CLIPPER_CLIPS_DIR = "$S\clips-r20" }
Invoke-Titles $Irl $IrlJson "--rank 1 --vertical --style face --captions" @{ CLIPPER_CLIPS_DIR = "$S\clips-r20" }
Invoke-Titles $Pod $PodJson "--rank 1 --vertical --style face --captions" @{ CLIPPER_CLIPS_DIR = "$S\clips-r20" }
Invoke-Titles $Pod $PodJson "--rank 1 --style face" @{ CLIPPER_CLIPS_DIR = "$S\clips-r20" }
Get-ChildItem "$S\clips-r20" -Recurse -Filter *.mp4 | ForEach-Object { ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 $_.FullName }
```

Pass: the first two runs exit 0 with `(vertical, face, captions)` and a
1080x1920 file. Watch both: the window holds still within a shot and
moves only as a cut, mostly at the video's own camera cuts, never as a
pan; brief movement is ignored, so a speaker leaning out for a moment is
partly cut off. On the IRL clip it locks on one person and may cut others
out. Captions sit on the
cropped frame. The third run is faster, its faces read from the cache. The
fourth exits 1 with `Error: --style face needs --vertical`. Record the
times and anything framed badly.

## Captions

Covers `clipper render --captions` ([ADR 0010](decisions/0010-burn-in-captions-from-segment-text.md),
[0011](decisions/0011-render-command-shape-and-failure-policy.md),
[0017](decisions/0017-word-timestamps-and-styled-captions.md)). Captions show
one to three words at a time, with the word being spoken in yellow. Run the
Rendering section's R0 first, in the same PowerShell window. These steps use
`$S`, `$Src`, `$Json`, `$r` and `testsrc.mp4` from R0.

Fixtures marked **constructed** are made by the commands in C0 and are not real
analysis output.

`CLIPPER_CLIPS_DIR` is set on the render command only and put back afterwards,
by `Invoke-RenderC` below. R0 sets it for the whole session. Before running
`uv run pytest` in this window, remove it:

```powershell
Remove-Item Env:CLIPPER_CLIPS_DIR -ErrorAction SilentlyContinue
```

`tests/unit/core/test_config.py::test_clips_dir_default` fails if it is set.

### C0. Setup

```powershell
$CapClips = "$S\clips-captions"

# Helper: like Invoke-Render, with --captions as a switch, and a clips folder
# passed in. CLIPPER_CLIPS_DIR is set for this one command and restored.
function Invoke-RenderC {
  param([string]$Video, [string]$ResultJson, [string]$Clips,
        [int[]]$Rank = @(), [switch]$Captions)
  $parts = @()
  foreach ($n in $Rank) { $parts += "--rank $n" }
  if ($Captions) { $parts += "--captions" }
  $old = $env:CLIPPER_CLIPS_DIR
  $env:CLIPPER_CLIPS_DIR = $Clips
  try {
    cmd /c "uv run clipper render `"$Video`" `"$ResultJson`" $($parts -join ' ') > `"$S\last.txt`" 2>&1"
    $script:Code = $LASTEXITCODE
  } finally {
    if ($null -eq $old) { Remove-Item Env:CLIPPER_CLIPS_DIR -ErrorAction SilentlyContinue }
    else { $env:CLIPPER_CLIPS_DIR = $old }
  }
  $script:Text = Get-Content "$S\last.txt" -Raw -Encoding UTF8
  "exit $Code"
  $Text
}
function Reset-CapClips { Remove-Item -Recurse -Force $CapClips -ErrorAction SilentlyContinue }
function Show-CapClips {
  if (Test-Path $CapClips) {
    Get-ChildItem $CapClips -Recurse -File |
      Select-Object @{n="Folder";e={$_.Directory.Name}}, Name, Length | Format-Table -AutoSize
  }
  else { "(clips folder does not exist)" }
}
function Get-CaptionTempCount {
  @(Get-ChildItem $env:TEMP -Directory -Filter "clipper-captions-*" -ErrorAction SilentlyContinue).Count
}

# Helper: one frame of a clip at T seconds (clip time).
function Save-Frame($Clip, [double]$T, $Png) {
  $ts = $T.ToString("F3", [Globalization.CultureInfo]::InvariantCulture)
  ffmpeg -hide_banner -loglevel error -y -ss $ts -i $Clip -frames:v 1 $Png
}

# Helper: the words that start inside a candidate, with clip-relative times.
# These are what the captions show, a chunk of up to three at a time.
function Get-Words($Res, [int]$Rank) {
  $c = $Res.candidates[$Rank - 1]
  # [double] casts: JSON whole numbers are integers, and [Math]::Min(10, 12.5)
  # would pick the integer overload and return 10.
  $cs = [double]$c.start_seconds; $ce = [double]$c.end_seconds
  foreach ($s in $Res.transcript.segments) {
    foreach ($w in $s.words) {
      $a = [double]$w.start_seconds
      if ($a -ge $cs -and $a -lt $ce) {
        $b = [Math]::Min([double]$w.end_seconds, $ce)
        [pscustomobject]@{ Start = $a - $cs; End = $b - $cs; Text = $w.text }
      }
    }
  }
}

# Helper (constructed fixtures): a result JSON with a transcript.
# Spans are "start-end"; Segments are "start|end|text". With -Words, each
# segment gets constructed word times, its text split on spaces and spread
# evenly over the segment, except the segments whose 0-based index is in
# -Whole. Without -Words no segment has words, like a v0.3 analysis. Written as
# UTF-8 without a BOM, which is what the JSON loader expects.
function New-CapResult {
  param([string]$Path, [string]$Source, [string[]]$Spans, [string[]]$Segments,
        [switch]$Words, [int[]]$Whole = @())
  $cands = @(foreach ($sp in $Spans) {
    $a, $b = $sp -split "-"
    @{ start_seconds = [double]$a; end_seconds = [double]$b; score = 0.5; reasons = @("constructed") }
  })
  $k = -1
  $segs = @(foreach ($sg in $Segments) {
    $k++
    $a, $b, $t = $sg -split "\|", 3
    $a = [double]$a; $b = [double]$b
    $ws = @()
    if ($Words -and $Whole -notcontains $k) {
      $parts = @($t.Trim() -split " +")
      $step = ($b - $a) / $parts.Count
      for ($n = 0; $n -lt $parts.Count; $n++) {
        $end = if ($n -eq $parts.Count - 1) { $b } else { [Math]::Round($a + ($n + 1) * $step, 3) }
        $ws += @{ start_seconds = [Math]::Round($a + $n * $step, 3); end_seconds = $end; text = $parts[$n] }
      }
    }
    @{ start_seconds = $a; end_seconds = $b; text = $t; words = $ws }
  })
  $doc = @{ source = $Source; transcript = @{ segments = $segs }; candidates = $cands }
  [IO.File]::WriteAllText($Path, (ConvertTo-Json -InputObject $doc -Depth 8),
    (New-Object Text.UTF8Encoding $false))
}

# Constructed: testsrc.mp4 (from R0), two candidates (12.5-20 and 25-35).
# Segment 10-12.5 only touches the first clip's start, 21-24 is between the
# clips, so the first clip has no speech: its last word starts before 12.5 s.
# The second clip has one segment.
New-CapResult "$S\cap-nospeech.json" "testsrc.mp4" @("12.5-20", "25-35") @(
  "0|5|far before both clips",
  "10|12.5|touches the start edge of clip one",
  "21|24|between the clips",
  "26|33|words inside the second clip") -Words

# Constructed: one candidate, seven segments of awkward text (C7). Each of the
# first six has up to three words, so it is one chunk. The seventh has no
# words, so it is one whole cue.
$e  = [string][char]0x00E9           # e with acute
$i  = [string][char]0x00EF           # i with diaeresis
$em = [char]::ConvertFromUtf32(0x1F600)   # outside the Basic Multilingual Plane
New-CapResult "$S\cap-awkward.json" "testsrc.mp4" @("12.5-20") @(
  "12.5|13.5|braces {a} <b>bold</b>",
  "13.5|14.5|arrow --> 5>3",
  "14.5|15.5|caf$e na${i}ve",
  "15.5|16.5|before $em after",
  "16.5|17.5|it's `"fine`" &",
  "17.5|18.5|path C:\new\table",
  "18.5|20|whole {cue} C:\new\table here") -Words -Whole 6

# Constructed: a 1280x720 source with the same name as testsrc.mp4 (C8).
New-Item -ItemType Directory -Force "$S\hd" | Out-Null
ffmpeg -hide_banner -loglevel error -y -f lavfi -i "testsrc=size=1280x720:rate=25:duration=40" `
  -f lavfi -i "sine=frequency=440:duration=40" -shortest `
  -c:v libx264 -pix_fmt yuv420p -c:a aac "$S\hd\testsrc.mp4"

# Constructed: a 600x1280 source, narrower than 9:16, with the same name (C8).
New-Item -ItemType Directory -Force "$S\narrow" | Out-Null
ffmpeg -hide_banner -loglevel error -y -f lavfi -i "testsrc=size=600x1280:rate=25:duration=40" `
  -f lavfi -i "sine=frequency=440:duration=40" -shortest `
  -c:v libx264 -pix_fmt yuv420p -c:a aac "$S\narrow\testsrc.mp4"

# Constructed: a result for testsrc.mp4 with speech in both clips, for C4 to C6.
New-CapResult "$S\cap-testsrc.json" "testsrc.mp4" @("12.5-20", "25-35") @(
  "13|19|first clip speech",
  "26|34|second clip speech") -Words

# Constructed: the same, with no word times, like a v0.3 analysis (C3).
New-CapResult "$S\cap-segments.json" "testsrc.mp4" @("12.5-20", "25-35") @(
  "13|19|first clip speech",
  "26|34|second clip speech")

"captions temp folders before any run: " + (Get-CaptionTempCount)
```

Pass: the `ffmpeg` commands print nothing, and the last line prints a number.
Record it. It should be `0`. If it is not, a folder was left by an earlier run,
and C4 must be read against that number and not against zero.

### C1. Render every candidate with captions (the first real run)

```powershell
Reset-CapClips
$t = Measure-Command { Invoke-RenderC $Src $Json $CapClips -Captions | Out-Null }
$Code; $Text
"seconds: $($t.TotalSeconds)"
Show-CapClips
Invoke-RenderC $Src $Json $CapClips | Out-Null   # same clips, no flag
$Text
```

Pass:

- Exit 0 for both runs.
- With `--captions`, each `Rendered #N ...` line ends with ` (captions)`. A
  `Note: #N ... has no speech in range` line is also valid for a candidate
  that has no speech in range. Record any. There is no `has no word timings`
  note: R0's analysis has word times.
- Without the flag the same lines have no suffix, and the file names are the
  same (ADR 0008).
- `Rendered X of X clips to <clips folder>\<stem>`, then a `Manifest:` line,
  in both runs.

Record the seconds. R1 gave the plain render time for the same candidates, so
the difference is the cost of the `subtitles` filter.

### C2. Caption words and timing match the transcript

Compares the burned-in words with the saved word times, on the real clip with
the highest rank, once on the source frame and once vertical (ADR 0017). The
source video may have text of its own in the picture. If it does, compare
against the transcript and not against that text.

```powershell
$words = @(Get-Words $r 1)
"words in rank 1: " + $words.Count
$words | Format-Table @{n="Start";e={"{0:N2}" -f $_.Start}}, @{n="End";e={"{0:N2}" -f $_.End}}, Text -AutoSize
$pick = @($words[0], $words[[int](($words.Count - 1) / 2)], $words[-1])
# The longest pause between two words, which is between two chunks.
$gap = 0..($words.Count - 2) | Sort-Object { $words[$_ + 1].Start - $words[$_].End } | Select-Object -Last 1
$GapT = ($words[$gap].End + $words[$gap + 1].Start) / 2
foreach ($frame in @(@{ n = "src"; f = "" }, @{ n = "vert"; f = "--vertical" })) {
  Reset-CapClips
  $old = $env:CLIPPER_CLIPS_DIR; $env:CLIPPER_CLIPS_DIR = $CapClips
  try { Invoke-RenderWith $Src $Json "--rank 1 --captions $($frame.f)" | Out-Null } finally { $env:CLIPPER_CLIPS_DIR = $old }
  "$($frame.n): exit $Code"
  $Clip = (Get-ChildItem $CapClips -Recurse -Filter "*.mp4" | Select-Object -First 1).FullName
  $n = 0
  foreach ($w in $pick) {
    $n++
    # The middle of the word, or 10 ms in for a word with no length.
    Save-Frame $Clip (($w.Start + [Math]::Max($w.End, $w.Start + 0.02)) / 2) "$S\word-$($frame.n)-$n.png"
    "word-$($frame.n)-$n.png : '" + $w.Text + "' in yellow, at {0:N2} s" -f $w.Start
  }
  Save-Frame $Clip $GapT "$S\word-$($frame.n)-gap.png"
  "word-$($frame.n)-gap.png : pause {0:N2}-{1:N2} s" -f $words[$gap].End, $words[$gap + 1].Start
  Remove-Item "$S\word-$($frame.n)-before.png" -ErrorAction SilentlyContinue
  if ($words[0].Start -gt 0.5) { Save-Frame $Clip ($words[0].Start / 2) "$S\word-$($frame.n)-before.png" }
}
Get-ChildItem "$S\word-*.png" | ForEach-Object { Invoke-Item $_.FullName }
```

`Invoke-RenderWith` is defined in R15, so run R15's helper block first.

Pass, for both `src` and `vert`:

- Exit 0.
- Each `word-*-N.png` shows the word printed for it in yellow, with up to two
  of its neighbours from the word list in white, in the order of the list. A
  chunk does not run past a word that ends in `.`, `?` or `!`.
- In `vert`, the caption is in the blurred fill below the picture. In `src`, it
  is near the bottom of the frame, inside it.
- `word-*-gap.png` shows no caption, if the pause is longer than about 0.1 s.
  Record its length.
- `word-*-before.png`, if made, shows no caption.
- The text has none of `{ } < >` in it.

Observe and record: whether the highlighted word matches what is heard when
you play the clip. Watch a few seconds of each. This is the first check of
the word times against the audio (ADR 0017).

### C3. A clip with no speech renders plain, with a note (constructed)

Uses `cap-nospeech.json`. Clip 1 (12.5 to 20 s) has no speech in range.
A segment that ends exactly at 12.5 s, with words that start before it, must
not count. Clip 2 (25 to 35 s) has one segment of five words, 1.4 s each.

```powershell
Reset-CapClips
Invoke-RenderC "$S\testsrc.mp4" "$S\cap-nospeech.json" $CapClips -Captions
Show-CapClips
Save-Frame "$CapClips\testsrc\testsrc_000012-000020.mp4" 3 "$S\nospeech-1.png"
Save-Frame "$CapClips\testsrc\testsrc_000025-000035.mp4" 4.5 "$S\nospeech-2.png"
Invoke-Item "$S\nospeech-1.png"; Invoke-Item "$S\nospeech-2.png"
```

Pass:

- Exit 0.
- A line `Note: #1 0:00:12-0:00:20 has no speech in range; rendered without
  captions`, before the `Rendered #1` line, and the `Rendered #1` line has no
  ` (captions)`.
- `Rendered #2 0:00:25-0:00:35 <path> (captions)`.
- `Rendered 2 of 2 clips to <clips folder>\testsrc`, then a `Manifest:` line.
- No `has no word timings` note.
- `nospeech-1.png` shows no caption. `nospeech-2.png` shows `words inside the`
  with `the` in yellow.

Then the same clips from `cap-segments.json`, which has no word times, as an
analysis saved by v0.3 does not:

```powershell
Reset-CapClips
Invoke-RenderC "$S\testsrc.mp4" "$S\cap-segments.json" $CapClips -Captions
Save-Frame "$CapClips\testsrc\testsrc_000012-000020.mp4" 3 "$S\segments-1.png"
Invoke-Item "$S\segments-1.png"
```

Pass: exit 0; `Note: #1 0:00:12-0:00:20 has no word timings; captions show
whole segments` before `Rendered #1 ... (captions)`, and the same for #2;
`segments-1.png` shows `first clip speech` in white, in the same font and
place as the word captions, with no word in yellow.

### C4. The temporary folder does not leak

`TemporaryDirectory(ignore_cleanup_errors=True)` hides a failed cleanup on
purpose, so only a count shows it. Compare with the number from C0.

```powershell
$base = Get-CaptionTempCount
Reset-CapClips
Invoke-RenderC "$S\testsrc.mp4" "$S\cap-testsrc.json" $CapClips -Captions | Out-Null
"after success: exit $Code, folders " + (Get-CaptionTempCount) + " (before: $base)"

# Constructed failure, as in R14: a folder where the second clip's file goes.
Reset-CapClips
New-Item -ItemType Directory -Force "$CapClips\testsrc\testsrc_000025-000035.mp4" | Out-Null
Invoke-RenderC "$S\testsrc.mp4" "$S\cap-testsrc.json" $CapClips -Captions
"after failure: folders " + (Get-CaptionTempCount) + " (before: $base)"
Show-CapClips
```

Pass:

- After the success run: exit 0, and the count equals `$base`.
- After the failure run: `Rendered #1 ... (captions)`, a `Failed: #2
  0:00:25-0:00:35: ffmpeg failed (exit ...): ...` line, `Rendered 1 of 2 clips`,
  exit 1, no traceback, and the count equals `$base`. Record ffmpeg's cause
  line. On R14 it was `Permission denied`; with a caption filter it may differ.
- The blocking folder is still there, and `testsrc_000012-000020.mp4` exists.

Fail: the count went up. The folder was not removed.

### C5. Paths with spaces, a relative path and a drive letter

ffmpeg runs with the temporary folder as its working directory, so the input
and output paths are made absolute first (ADR 0010). These runs try to break
that. Every path here is on the `C:` drive already, which covers the drive
letter in the paths ffmpeg sees. Run from the repo root.

```powershell
# a) a video path with spaces, and a clips folder with spaces
New-Item -ItemType Directory -Force "$S\with space" | Out-Null
Copy-Item "$S\testsrc.mp4" "$S\with space\testsrc.mp4"
Invoke-RenderC "$S\with space\testsrc.mp4" "$S\cap-testsrc.json" "$S\clips with space" -Rank 1 -Captions
Get-ChildItem "$S\clips with space" -Recurse -File | Select-Object Name, Length

# b) a relative video path and a relative clips folder
$RelVideo = Resolve-Path -Relative "$S\testsrc.mp4"
$RelClips = ".\data\clips-captions-manual"
Remove-Item -Recurse -Force $RelClips -ErrorAction SilentlyContinue
"relative video: $RelVideo"
Invoke-RenderC $RelVideo "$S\cap-testsrc.json" $RelClips -Rank 1 -Captions
Get-ChildItem $RelClips -Recurse -File | Select-Object Name, Length
Remove-Item -Recurse -Force $RelClips

"folders left: " + (Get-CaptionTempCount)
```

Pass: for both a) and b), exit 0, a `Rendered #1 ... (captions)` line, and a
non-empty `testsrc_000012-000020.mp4` in a `testsrc` folder. In b) the clip is
in the repo's `data\clips-captions-manual\testsrc` and not in the temporary
folder. The step removes
that folder at the end. The last line equals `$base` from C4.

Optional: if you have a second drive, repeat a) with a clips folder on it
(`D:\clipper-test`, say) and record the result. Write "no second drive" if you
do not.

### C6. Plain and captioned clips overwrite each other

A captioned clip has the same file name as a plain one (ADR 0008), so
`--captions` overwrites an earlier plain clip of the same span, and the other
way round.

```powershell
Reset-CapClips
Invoke-RenderC "$S\testsrc.mp4" "$S\cap-testsrc.json" $CapClips -Rank 2 | Out-Null
$f = "$CapClips\testsrc\testsrc_000025-000035.mp4"
$h1 = (Get-FileHash $f).Hash; $s1 = (Get-Item $f).Length
Save-Frame $f 4.5 "$S\over-1.png"

Invoke-RenderC "$S\testsrc.mp4" "$S\cap-testsrc.json" $CapClips -Rank 2 -Captions | Out-Null
"captioned: exit $Code, hash changed: " + ((Get-FileHash $f).Hash -ne $h1) + ", size $s1 -> " + (Get-Item $f).Length
Save-Frame $f 4.5 "$S\over-2.png"

Invoke-RenderC "$S\testsrc.mp4" "$S\cap-testsrc.json" $CapClips -Rank 2 | Out-Null
"plain again: exit $Code"
Save-Frame $f 4.5 "$S\over-3.png"
"files: " + @(Get-ChildItem $CapClips -Recurse -File).Count
1..3 | ForEach-Object { Invoke-Item "$S\over-$_.png" }
```

Pass: `hash changed: True`, `files: 2`, the clip and `manifest.json` (no
numbered or suffixed copies), `over-1.png` and `over-3.png` show no caption, and `over-2.png` shows
`second clip speech` with `clip` in yellow.

### C7. Awkward text (constructed)

Uses `cap-awkward.json`. `build_ass` removes `{ } < > \` from spoken text
(ADRs 0010 and 0017), so the expected text on screen is below. The builder's
own colour tags use braces and backslashes, so a word that kept one could
break the tag around it. The emoji is outside the Basic Multilingual Plane,
and two segments have backslashes, which libass reads as a line break (`\n`)
when they are left in. Segments 1 to 6 have constructed word times, one chunk
each. Each frame is taken in the middle of the word marked yellow. Segment 7
has no words, so it is one whole cue.

```powershell
Reset-CapClips
Invoke-RenderC "$S\testsrc.mp4" "$S\cap-awkward.json" $CapClips -Captions
$f = "$CapClips\testsrc\testsrc_000012-000020.mp4"
$mid = 0.5, 1.5, 2.75, 3.5, 4.5, 5.75, 6.75
for ($k = 0; $k -lt 7; $k++) { Save-Frame $f $mid[$k] "$S\awk$($k + 1).png"; Invoke-Item "$S\awk$($k + 1).png" }
```

| Frame | Expected on screen | In yellow |
|-------|--------------------|-----------|
| `awk1.png` | `braces a bbold/b` | `a` |
| `awk2.png` | `arrow -- 53` | `--` |
| `awk3.png` | `café naïve`, with their accents | `naïve` |
| `awk4.png` | `before`, the emoji, `after` | the emoji. Observe and record it |
| `awk5.png` | `it's "fine" &` | `"fine"` |
| `awk6.png` | `path C:newtable`, on one line, with no gap and no letter missing | `C:newtable` |
| `awk7.png` | `whole cue C:newtable here` | nothing |

Pass: exit 0, a `Note: #1 ... has no word timings` line (from segment 7), and
all seven frames show their text with only the word in the last column in
yellow, and the words after it white again. The words around the emoji in
`awk4.png` must be present. The emoji itself may be missing or a box, since
the captions use Arial. Record what you saw.

Fail `awk6.png` or `awk7.png` if it shows `C: ew` or any `\`. That is the
finding the backslash amendment in ADR 0010 fixed: the removal of `\` in
`_MARKUP` has been lost. Fail any frame that shows `c&H`, `\r` or a brace: a
colour tag was broken.

### C8. How it looks (observe and record)

The style is in `core/captions.py`: Arial bold, white with a black outline,
the active word yellow, bottom centre. It was chosen on frames of one real
video (ADR 0017) and is not tuned. Plain clips use a 1920x1080 canvas that
libass scales to the source frame, so a source that is not 16:9 could stretch
the text. This step records what it looks like on the constructed 640x360
`testsrc.mp4`, the 1280x720 and 600x1280 copies from C0, and the vertical
frame. It has one fail condition.

```powershell
$sd = "$CapClips\testsrc\testsrc_000012-000020.mp4"
foreach ($v in @(@{ n = "640"; p = "$S\testsrc.mp4" }, @{ n = "1280"; p = "$S\hd\testsrc.mp4" },
                 @{ n = "narrow"; p = "$S\narrow\testsrc.mp4" })) {
  Reset-CapClips
  Invoke-RenderC $v.p "$S\cap-awkward.json" $CapClips -Captions | Out-Null
  Copy-Item $sd "$S\style-$($v.n).mp4"
}
Reset-CapClips
$old = $env:CLIPPER_CLIPS_DIR; $env:CLIPPER_CLIPS_DIR = $CapClips
try { Invoke-RenderWith "$S\testsrc.mp4" "$S\cap-awkward.json" "--captions --vertical" | Out-Null } finally { $env:CLIPPER_CLIPS_DIR = $old }
Copy-Item $sd "$S\style-vertical.mp4"
foreach ($n in "640", "1280", "narrow", "vertical") {
  Save-Frame "$S\style-$n.mp4" 0.625 "$S\style-$n-a.png"
  Save-Frame "$S\style-$n.mp4" 3.125 "$S\style-$n-b.png"
  Invoke-Item "$S\style-$n-a.png"; Invoke-Item "$S\style-$n-b.png"
}
```

`-a` frames show `braces a bbold/b` with `a` in yellow, and `-b` frames show
the `before` chunk with `before` in yellow.

Record: is the text readable on the test picture, how big it is next to the
frame at each size, how many lines it takes, and whether any line touches the
frame edge. On `narrow`, record whether round letters (`o`, `a`, `e`) look
round or squashed. Fail the step only if text is cut off by the frame. Size,
placement and stretching are a judgement for the maintainer, and a change to
them is a code change, not part of a release.

## LLM detector

Covers `--detector llm` ([ADR 0014](decisions/0014-local-llm-detector-via-ollama.md),
[0013](decisions/0013-choose-the-detector-with-a-cli-option.md),
[0015](decisions/0015-determinism-is-per-detector.md)). The detector and CLI
tests use a fake generator and a mocked `urllib.request.urlopen`, so nothing in
CI talks to a real Ollama server or model. This section checks that Ollama
accepts the request `OllamaClient` sends, that the error messages read well
against the real server, how long scoring takes, whether two runs agree, and
which model to use.

It reruns detectors on saved analyses with `eval`, so nothing is transcribed.
L1 and L3 run `analyze` on the short video, which takes the transcript from
the cache and writes to the scratch folder. It needs the two saved analyses
and their labels files from V3 item 0: `data/output/test_input.json` with
`data/labels/test_input.json`, and `data/output/oFbD0Ot7nQ8.json` with
`data/labels/oFbD0Ot7nQ8.json`, plus `data/test_input.mp4` and its cached
transcript in `data/cache/`. It does not need the Setup section above.

`CLIPPER_*` variables are set for one command and put back afterwards by
`Invoke-Llm` below. Nothing in this section leaves one set.

### L0. Setup

1. Install Ollama for Windows from <https://ollama.com>. It runs as a
   background server with a tray icon.
2. Pick two or three instruction-tuned models from Ollama's library that fit
   in the GPU's 12 GB at their default quantisation: roughly 14 billion
   parameters or fewer, ideally from different families. Pull each one:

   ```powershell
   ollama pull <model>
   ```

3. Record the versions and exact model tags:

   ```powershell
   ollama --version
   ollama list
   nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv
   ```

4. Check that the server answers on the address `CLIPPER_LLM_URL` defaults to.
   It should list the pulled models:

   ```powershell
   (Invoke-RestMethod http://127.0.0.1:11434/api/tags).models | Select-Object name, size
   ```

5. In a fresh PowerShell window at the repo root:

   ```powershell
   $S = Join-Path $env:TEMP "manual-test-clipper-llm"
   New-Item -ItemType Directory -Force $S | Out-Null
   $M1 = "<first model tag>"        # from ollama list
   $M2 = "<second model tag>"
   $M3 = "<third model tag>"        # or drop it from the loops below

   # Keep rich from wrapping at 80 columns. Not a CLIPPER_ variable; L6
   # removes it.
   $env:COLUMNS = "500"

   # Helper: runs `clipper <Arguments>` with CLIPPER_LLM_MODEL set to $Model
   # (unset if $Model is "", so the default applies), plus any extra CLIPPER_
   # variables, for this one
   # command, and puts every variable back afterwards. Keeps the exit code,
   # the seconds taken and the text, and prints them.
   function Invoke-Llm {
     param([string]$Model, [string]$Arguments, [hashtable]$Extra = @{})
     $vars = @{ CLIPPER_LLM_MODEL = $Model } + $Extra
     $old = @{}
     foreach ($k in $vars.Keys) {
       $old[$k] = [Environment]::GetEnvironmentVariable($k, "Process")
       $v = if ($vars[$k] -eq "") { $null } else { $vars[$k] }
       [Environment]::SetEnvironmentVariable($k, $v, "Process")
     }
     try {
       $t = Measure-Command {
         cmd /c "uv run clipper $Arguments > `"$S\last.txt`" 2>&1"
       }
       $script:Code = $LASTEXITCODE
     } finally {
       foreach ($k in $vars.Keys) {
         [Environment]::SetEnvironmentVariable($k, $old[$k], "Process")
       }
     }
     $script:Seconds = [Math]::Round($t.TotalSeconds, 1)
     $script:Text = Get-Content "$S\last.txt" -Raw -Encoding UTF8
     "exit $Code, $Seconds s"
     $Text
   }
   ```

Check that each model variable names a pulled model. A placeholder left in
place, such as `<first model tag>`, fails every later step with
`HTTP 400 ... invalid model name`. Each line should end in `True`:

```powershell
foreach ($m in $M1, $M2, $M3) { ollama show $m | Out-Null; "$m resolves: $($LASTEXITCODE -eq 0)" }
```

Check that no `CLIPPER_` variable is set before you start. This should print
nothing:

```powershell
Get-ChildItem Env:CLIPPER_* -ErrorAction SilentlyContinue
```

### L1. First real run: `analyze --detector llm`

Unload the model first, so the time includes loading it, then run on the 5:14
speech video. The transcript comes from the cache. Output goes to the scratch
folder, so `data/output/test_input.json` keeps the heuristic's result.

```powershell
ollama stop $M1
Invoke-Llm $M1 "analyze data\test_input.mp4 --detector llm -v" @{ CLIPPER_OUTPUT_DIR = "$S\out" }
ollama ps
(Get-Content "$S\out\test_input.json" -Raw | ConvertFrom-Json).detector
```

Pass:

- Exit 0.
- The log shows `Scored 18 of 18 windows`. ADR 0014's 7a amendment counted 18
  windows on this transcript.
- A table of up to five candidates. Each Why cell is one sentence from the
  model, not the heuristic's signal names.
- `Detector: llm:<model tag>`, and the saved JSON's `detector` is the same
  string.

Record:

- The seconds. This is the first time measured for the LLM detector, and it
  includes loading the model.
- The table, as printed.
- What `ollama ps` shows for the model, in particular whether it runs on the
  GPU, the CPU, or split between them. Anything but all-GPU means the model
  did not fit in VRAM, and the timings below are not GPU timings.

If this fails with `Model reply for window ... is not a valid score` or
`Unexpected response from Ollama`, record the full message and stop: the
request or the schema does not match what this Ollama version expects.

### L2. Errors against the real server (observe and record)

Each must exit 1 with one `Error: ...` line and no traceback. Record each
message exactly.

The empty-model error (ADR 0013) is not checked here. `CLIPPER_LLM_MODEL`
defaults to `qwen3.5:9b`, so only an empty value reaches it, and Windows does
not keep an empty environment variable. The CLI unit tests cover it.

```powershell
# a. Malformed model name.
Invoke-Llm "<not a model tag>" "eval data\output\test_input.json --detector llm"

# b. Model not pulled. The tag is deliberately one that does not exist.
Invoke-Llm "no-such-model-clipper:latest" "eval data\output\test_input.json --detector llm"

# c. Nothing listening on the URL.
Invoke-Llm $M1 "eval data\output\test_input.json --detector llm" @{ CLIPPER_LLM_URL = "http://127.0.0.1:1" }
```

Then quit Ollama from its tray icon, confirm the server is gone, and run d:

```powershell
Invoke-RestMethod http://127.0.0.1:11434/api/tags   # should fail now

# d. Ollama not running.
Invoke-Llm $M1 "eval data\output\test_input.json --detector llm"
```

Start Ollama again from the Start menu, and rerun step 4 of L0 before going
on.

Pass:

- a: `Error: Ollama at http://127.0.0.1:11434 returned HTTP 400 for model
  '<not a model tag>': invalid model name`. Ollama 0.40.13 answered 400 with
  that text for `<first model tag>`; no pull hint is added on a 400.
- b: `Error: Ollama at http://127.0.0.1:11434 returned HTTP 404 for model
  'no-such-model-clipper:latest': model 'no-such-model-clipper:latest' not
  found. Is the model pulled? (`ollama pull no-such-model-clipper:latest`)`,
  as Ollama 0.40.13 answered. Record it again after upgrading Ollama.
- c and d: `Error: Cannot reach Ollama at <url>: <reason>. Is it running?`.
  Record the reason text, which comes from Windows.
- c and d return within a few seconds. A long wait before the error means the
  connection timed out instead of being refused; record the seconds.

### L3. Repeatability on the short video (ADR 0015)

The same transcript and model, twice, for each model. This uses `analyze`, not
`eval`, because `eval` prints only each candidate's span and a two-decimal
score: two runs that differ in a reason or in the third decimal would print the
same. The saved JSON holds every score in full and every reason, and the
transcript comes from the cache, so the two files differ only if the
candidates do. L5 repeats this for the chosen model on the long video.

```powershell
foreach ($m in $M1, $M2, $M3) {
  foreach ($r in "a", "b") {
    Invoke-Llm $m "analyze data\test_input.mp4 --detector llm" @{ CLIPPER_OUTPUT_DIR = "$S\rep-$r"; CLIPPER_TOP_N = "30" } | Out-Null
  }
  $a = Get-Content "$S\rep-a\test_input.json" -Raw -Encoding UTF8
  $b = Get-Content "$S\rep-b\test_input.json" -Raw -Encoding UTF8
  "$m identical: $($a -ceq $b)"
  if ($a -cne $b) { Compare-Object ($a -split "`n") ($b -split "`n") }
}
```

There is no pass or fail. ADR 0015 makes repeatability an aim for the LLM, not
a guarantee. Record, per model, whether the two files were identical and, if
not, the lines that differ. A difference triggers ADR 0015's "revisit when"
(repeated runs in `eval`).

### L4. Short video: compare the models with `eval`

The labels on `test_input.json` cover the whole speech, so this is a smoke test
of quality, not a measurement. The first command is the heuristic, as the
baseline.

```powershell
Invoke-Llm "" "eval data\output\test_input.json"
foreach ($m in $M1, $M2, $M3) {
  Invoke-Llm $m "eval data\output\test_input.json --detector llm"
}
```

Record, per model: the `Recall:` and `Precision:` lines, the seconds, and the
candidates table. ADR 0014's 7a amendment asks how often scores tie at the
top-N cut: count the rows whose Score equals the last row's. Scores print with
two decimals, so a tie in print may not be a tie in the score itself.

### L5. Long video: runtime, results and ties

`oFbD0Ot7nQ8.json` is 5:04:01 and has 1,215 windows, one request each. `eval`
prints nothing until scoring finishes, so judge progress from `ollama ps` or
the GPU load in Task Manager.

Run at top 30. `select_top` and the one-to-one matching are both greedy in
rank order, so the first five rows of a top-30 run are exactly the top-5 run's
candidates and matches. Read the top-5 numbers off those rows instead of
running again.

Run the first model alone. If it takes longer than you would wait for every
video, stop it with Ctrl+C, record the time and how far it got (the GPU going
idle marks the end), and skip the second block. That is ADR 0014's "too slow"
trigger.

```powershell
function Save-Long([string]$Model) {
  $Text | Set-Content "$S\long-$($Model -replace '[:/\\]', '_').txt" -Encoding utf8
}
Invoke-Llm $M1 "eval data\output\oFbD0Ot7nQ8.json --detector llm" @{ CLIPPER_TOP_N = "30" }
Save-Long $M1
```

Then the other models, and the heuristic:

```powershell
foreach ($m in $M2, $M3) {
  Invoke-Llm $m "eval data\output\oFbD0Ot7nQ8.json --detector llm" @{ CLIPPER_TOP_N = "30" }
  Save-Long $m
}
Invoke-Llm "" "eval data\output\oFbD0Ot7nQ8.json" @{ CLIPPER_TOP_N = "30" }
```

Record, per model:

- The seconds, and the seconds per window (seconds / 1,215).
- At top 30: the `Recall:` and `Precision:` lines, with the repeats.
- At top 5, from the first five rows of the candidates table: labels hit
  (recall); rows whose Hits label shows a label number not marked
  `(repeat of #k)` (precision); and rows marked as repeats.
- Ties: rows whose Score equals row 5's, and rows whose Score equals row 30's.

The last command reprints the heuristic's numbers for the same files, to set
beside the ones in `CLAUDE.md` ("In progress").

Then run the chosen model once more and compare with its first run, for the
long-video repeatability check:

```powershell
$m = "<chosen model tag>"
Invoke-Llm $m "eval data\output\oFbD0Ot7nQ8.json --detector llm" @{ CLIPPER_TOP_N = "30" } | Out-Null
$first = Get-Content "$S\long-$($m -replace '[:/\\]', '_').txt" -Raw -Encoding utf8
"identical: $($first.TrimEnd() -ceq $Text.TrimEnd())"
```

This compares spans and two-decimal scores only (see L3). Rerunning `analyze`
on the long video would compare reasons too, but it needs the video and its
cached transcript; it is not part of this check.

### L6. Clean up

```powershell
Get-ChildItem Env:CLIPPER_* -ErrorAction SilentlyContinue   # should print nothing
Remove-Item Env:COLUMNS
Remove-Item -Recurse -Force $S
```

Out of scope here: models that do not fit in VRAM, a remote Ollama server,
parallel requests, and caching scores between `eval` runs. Each is a separate
item if the timings in L5 call for it.

## Fetch

Covers `clipper fetch` ([ADR 0020](decisions/0020-fetch-a-video-by-url.md)).
The tests check the URL rules in pure code, mock `subprocess.run` for the
yt-dlp adapter, and pass a fake downloader to the CLI, so nothing in CI runs
yt-dlp or downloads. This section checks that the real yt-dlp accepts the
command, what lands on disk, and how failures read.

Downloading from YouTube or Twitch is against their terms (ADR 0020). Use
videos you have the right to download, such as your own or ones under a
Creative Commons licence. Short ones are quicker: a few minutes with speech
is enough. For Twitch, a highlight is shorter than a past broadcast and has
the same `twitch.tv/videos/<number>` URL.

### D0. Setup

yt-dlp must be on `PATH`. With uv: `uv tool install yt-dlp`, then
`uv tool update-shell` once, and open a new terminal. In a fresh PowerShell
window at the repo root:

```powershell
yt-dlp --version
$YouTubeUrl = "<a short public YouTube video URL>"
$TwitchUrl  = "<a short public Twitch VOD or highlight URL>"
$S = Join-Path $env:TEMP "manual-test-clipper-fetch"
New-Item -ItemType Directory -Force $S | Out-Null
$env:COLUMNS = "500"
$env:CLIPPER_VIDEOS_DIR = "$S\videos"
$env:CLIPPER_CACHE_DIR  = "$S\cache"
$env:CLIPPER_OUTPUT_DIR = "$S\out"
$env:CLIPPER_CLIPS_DIR  = "$S\clips"

# Helper: runs `clipper <Arguments>`, keeps the exit code, the seconds taken
# and the text, and prints them. yt-dlp's progress goes to the same file.
function Invoke-Fetch {
  param([string]$Arguments)
  $t = Measure-Command {
    cmd /c "uv run clipper $Arguments > `"$S\last.txt`" 2>&1"
  }
  $script:Code = $LASTEXITCODE
  $script:Seconds = [Math]::Round($t.TotalSeconds, 1)
  $script:Text = Get-Content "$S\last.txt" -Raw -Encoding UTF8
  "exit $Code, $Seconds s"
  $Text
}
# Helper: what ffprobe says about a fetched file's streams and length.
function Show-Video([string]$Path) {
  ffprobe -v error -show_entries "stream=codec_type,codec_name,width,height:format=duration" -of compact=p=0 "$Path"
}
```

Pass: `yt-dlp --version` prints a version. Record it.

### D1. A YouTube video

```powershell
Invoke-Fetch "fetch `"$YouTubeUrl`""
Get-ChildItem "$env:CLIPPER_VIDEOS_DIR" | Select-Object Name, Length | Format-Table -AutoSize
$YouTubeFile = (Get-ChildItem "$env:CLIPPER_VIDEOS_DIR" -Filter "*.mp4" | Select-Object -First 1).FullName
Show-Video $YouTubeFile
```

Pass: exit 0, yt-dlp's progress lines, then `Saved: <videos folder>\<id>.mp4`,
where `<id>` is the 11-character ID from the URL. The folder holds that one
`.mp4`, its `<id>.info.json` (step D10a) and no `.part` or other files. The streams include video and audio, and
the height is at most 1080. The video codec may be AV1 or H.264; `render`
re-encodes either. Record the time, the size, the codecs and the resolution.
yt-dlp may warn that no JavaScript runtime was found; record it, and whether
the download still worked.

### D2. A Twitch VOD

```powershell
Invoke-Fetch "fetch `"$TwitchUrl`""
$TwitchFile = (Get-ChildItem "$env:CLIPPER_VIDEOS_DIR" -Filter "v*.mp4" | Select-Object -First 1).FullName
Show-Video $TwitchFile
```

Pass: exit 0 and `Saved: <videos folder>\v<number>.mp4`, with the number from
the URL. Streams as in D1. Record the time, the size, the codecs and the
resolution.

### D3. A second fetch reuses the file

```powershell
$h = (Get-FileHash $YouTubeFile).Hash
Invoke-Fetch "fetch `"$YouTubeUrl`""
"unchanged: " + ((Get-FileHash $YouTubeFile).Hash -eq $h)
```

Pass: exit 0 in a few seconds, `Already fetched: <the D1 path>`, no yt-dlp
output, and `unchanged: True`.

### D4. Playlist, channel and other URLs are refused

```powershell
Invoke-Fetch "fetch `"https://www.youtube.com/playlist?list=PLclipperManualTest`""
Invoke-Fetch "fetch `"https://www.youtube.com/@youtube`""
Invoke-Fetch "fetch `"https://www.twitch.tv/twitch`""
Invoke-Fetch "fetch `"https://vimeo.com/1`""
"files: " + @(Get-ChildItem "$env:CLIPPER_VIDEOS_DIR").Count
```

Pass: each exits 1 at once with one `Error:` line: `Playlist URLs are not
fetched`, `Not a single YouTube video URL`, `Only Twitch VOD URLs` and
`Not a YouTube video or Twitch VOD URL`. The first three cite
`docs/decisions/0020`. `files:` is the same count as after D2.

### D5. An unavailable video (observe and record)

The ID is made up; if it happens to exist, pick another.

```powershell
Invoke-Fetch "fetch `"https://www.youtube.com/watch?v=zzzzzzzzzz0`""
Get-ChildItem "$env:CLIPPER_VIDEOS_DIR" -Filter "zzzzzzzzzz0*"
```

Pass: exit 1, `Error: yt-dlp failed (exit 1): ...` with yt-dlp's own
`ERROR:` line, no traceback, and no file for that ID. Record the message.

### D6. yt-dlp missing from `PATH`

```powershell
$oldPath = $env:PATH
$dir = Split-Path (Get-Command yt-dlp).Source
$env:PATH = ($env:PATH -split ";" | Where-Object { $_.TrimEnd("\") -ne $dir.TrimEnd("\") }) -join ";"
Invoke-Fetch "fetch `"https://www.youtube.com/watch?v=zzzzzzzzzz1`""
$env:PATH = $oldPath
```

Pass: exit 1, `Error: 'yt-dlp' not found on PATH. Install it with ...`, with
the `uv tool install` hint.

### D7. `analyze` reads a fetched file

```powershell
Invoke-Fetch "analyze `"$YouTubeFile`""
```

Pass: exit 0, a table of candidates, and `Saved:` with
`<output folder>\<id>.json`, the same stem as the video. A video with less
than about a minute of speech may give no candidates; that is not a fetch
failure.

### D8. `drafts` from a URL to a folder of drafts

Covers [ADR 0022](decisions/0022-one-command-to-drafts.md). Needs Ollama
running with the default model (L0), since titles are on by default. Use a
second short video with speech.

```powershell
$DraftsUrl = "<another short public YouTube video URL>"
$env:CLIPPER_CLIPS_DIR = "$S\clips"
Invoke-Fetch "drafts `"$DraftsUrl`""
$id = (Get-ChildItem "$env:CLIPPER_VIDEOS_DIR" -Filter "*.mp4" | Sort-Object LastWriteTime | Select-Object -Last 1).BaseName
$m = Get-Content "$env:CLIPPER_CLIPS_DIR\$id\manifest.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$m | Select-Object source, analysis, detector, vertical, captions, titles | Format-List
$m.clips | Format-List rank, file, captions, title, hook, error
$clip = (Get-ChildItem "$env:CLIPPER_CLIPS_DIR\$id" -Filter "*.mp4" | Select-Object -First 1).FullName
Show-Video $clip
ffmpeg -hide_banner -loglevel error -y -ss 5 -i "$clip" -frames:v 1 "$S\drafts.png"
Invoke-Item "$S\drafts.png"
```

Pass: exit 0. The output shows the fetch (`Saved:`), the candidates table,
`Saved: <output folder>\<id>.json`, two `Rendered #N ...` lines per clip,
`(vertical, captions, titles)` and `(vertical, face, captions, titles)`,
then its title and hook, `Manifest: ...`, and last `Drafts: <clips
folder>\<id>`. The folder holds `<name>.mp4` and `<name>.face.mp4` per
clip (only the first if a note says the clip has no face). The manifest has
`vertical`, `captions` and `titles` true and `styles` fit and face,
`analysis` is that JSON, and every clip has a `file`, a `title`, a `hook`
and its `variants`. The clip is H.264 1080x1920 and the frame shows the
fitted picture on a blurred fill. Record the time.

### D9. `drafts` on a long stream, timed (criterion 5)

The long-stream time for [vision criterion 5](vision.md). From a local file,
so the time is not a download's. The cache folder is the scratch one, so the
transcription is cold. It takes about as long as a cold `analyze` (21 minutes
for a 5-hour stream on the reference machine) plus rendering.

```powershell
$LongVideo = "<a long local video, e.g. data\videos\oFbD0Ot7nQ8.mp4>"
Invoke-Fetch "drafts `"$LongVideo`"" | Select-Object -Last 12
$stem = [IO.Path]::GetFileNameWithoutExtension($LongVideo)
(Get-Content "$env:CLIPPER_CLIPS_DIR\$stem\manifest.json" -Raw -Encoding UTF8 | ConvertFrom-Json).clips |
  Format-Table rank, start_seconds, end_seconds, file, title, error -AutoSize
```

Pass: exit 0, `Drafts: ...` last, and every clip has a `file` and a title.
Record the video's length and the total time.

### D10. Ollama unreachable: titles fail, `--no-titles` renders

`CLIPPER_LLM_URL` points at a port nothing listens on, which is what a
stopped Ollama looks like to Clipper. The transcript is cached from D8.
The heuristic picks, since the default LLM detector needs Ollama too
(ADR 0014, item 11 amendment).

```powershell
$env:CLIPPER_LLM_URL = "http://127.0.0.1:1"
Invoke-Fetch "drafts `"$DraftsUrl`" --detector heuristic" | Select-Object -Last 8
"clips: " + @(Get-ChildItem "$env:CLIPPER_CLIPS_DIR\$id" -Filter "*.mp4").Count
Invoke-Fetch "drafts `"$DraftsUrl`" --detector heuristic --no-titles" | Select-Object -Last 4
Remove-Item Env:CLIPPER_LLM_URL
```

Pass: the first run exits 1, with a `Failed: #N ...: Cannot reach Ollama at
http://127.0.0.1:1 ...` line per clip, `Rendered 0 of N clips` and
`Drafts: ...` last. `clips:` still counts D8's files, since nothing was
deleted. The second run exits 0 with every clip rendered and no title lines.

### D10a. Video info beside the video

```powershell
Get-ChildItem "$env:CLIPPER_VIDEOS_DIR" -Filter "*.info.json" | Select-Object Name, Length | Format-Table -AutoSize
Get-Content ($TwitchFile -replace '\.mp4$', '.info.json') -Encoding UTF8
Get-Content ($YouTubeFile -replace '\.mp4$', '.info.json') -Encoding UTF8
Remove-Item ($YouTubeFile -replace '\.mp4$', '.info.json')
Invoke-Fetch "fetch `"$YouTubeUrl`""
Remove-Item ($YouTubeFile -replace '\.mp4$', '.info.json')
$oldPath = $env:PATH
$dir = Split-Path (Get-Command yt-dlp).Source
$env:PATH = ($env:PATH -split ";" | Where-Object { $_.TrimEnd("\") -ne $dir.TrimEnd("\") }) -join ";"
Invoke-Fetch "fetch `"$YouTubeUrl`""
$env:PATH = $oldPath
```

Pass: D1 and D2 printed `Video info: <videos folder>\<id>.info.json` after
`Saved:`. Each file is a few KB at most and holds `platform`, `video_id`,
`title`, `channel`, `upload_date`, `duration_seconds` and `description`:
for Twitch the channel is the streamer's login and the description is
empty; for YouTube the channel is the channel's name. The first fetch after
deleting prints `Already fetched:`, no download progress, then `Video info:`
again. With yt-dlp off `PATH` it exits 0 with `Already fetched:` and
`Warning: no video info saved: 'yt-dlp' not found on PATH ...`, and no
`.info.json` is written. Record the two files' channel and title.

### D12. `drafts` on a list of sources

Covers [ADR 0022](decisions/0022-one-command-to-drafts.md)'s list
amendment. Uses D1's and D2's fetched videos, so nothing is downloaded
except in step 3.

```powershell
@"
# D12 queue
$YouTubeFile | Some name
https://www.youtube.com/playlist?list=PLclipperManualTest
$TwitchFile
"@ | Set-Content "$S\queue.txt" -Encoding UTF8
Invoke-Fetch "drafts --list `"$S\queue.txt`" --no-titles --style fit --detector heuristic"
(Get-Content "$S\queue.txt") -notmatch "playlist" | Set-Content "$S\queue.txt" -Encoding UTF8
Invoke-Fetch "drafts --list `"$S\queue.txt`" --no-titles --style fit --detector heuristic"
Invoke-Fetch "drafts `"$TwitchUrl`" https://www.youtube.com/watch?v=zzzzzzzzzz1 --no-titles --style fit --detector heuristic"
```

Pass:

1. The first run exits 1 at once with `Error: Line 3: Playlist URLs are not
   fetched ...`, the note that the list's about text is not used, and no
   `Source` line.
2. The second exits 0: `Source 1 of 2: ...` and `Source 2 of 2: ...`, each
   with its `Drafts:` line, then a `Drafts` table with both folders and
   their clip counts.
3. The third exits 1: the Twitch VOD renders, the unavailable YouTube ID
   fails with yt-dlp's message, and the table shows that source's error.

### D11. Clean up

```powershell
Remove-Item Env:CLIPPER_VIDEOS_DIR, Env:CLIPPER_CACHE_DIR, Env:CLIPPER_OUTPUT_DIR, Env:CLIPPER_CLIPS_DIR, Env:COLUMNS
Get-ChildItem Env:CLIPPER_* -ErrorAction SilentlyContinue   # should print nothing
Remove-Item -Recurse -Force $S
```

## Labels from Twitch clips

Covers `clipper labels`
([ADR 0012](decisions/0012-evaluate-detectors-against-hand-labels.md), its
Twitch clips amendment, and
[ADR 0020](decisions/0020-fetch-a-video-by-url.md)'s clip lookups). The tests
mock yt-dlp, so this section checks that the real yt-dlp returns a clip's
title and length, what the labels file looks like, and the first `eval`
against it. Nothing is downloaded.

### T0. Setup

yt-dlp must be on `PATH` (D0). You need a links file for one Twitch VOD,
written by hand: per line, a clip's URL and its "Watch full video" URL (with
`?t=`). For T5 you also need that VOD's saved analysis, from `analyze` on the
fetched VOD. In a fresh PowerShell window at the repo root:

```powershell
yt-dlp --version
$LinksFile = "<a links file, e.g. data\labels\links\v2917438534.txt>"
$Analysis  = "<that VOD's analysis JSON, e.g. data\output\v2917438534.json>"
$S = Join-Path $env:TEMP "manual-test-clipper-labels"
New-Item -ItemType Directory -Force $S | Out-Null
$env:COLUMNS = "500"
$env:CLIPPER_LABELS_DIR = "$S\labels"

# Helper: runs `clipper <Arguments>`, keeps the exit code, the seconds taken
# and the text, and prints them.
function Invoke-Labels {
  param([string]$Arguments)
  $t = Measure-Command {
    cmd /c "uv run clipper $Arguments > `"$S\last.txt`" 2>&1"
  }
  $script:Code = $LASTEXITCODE
  $script:Seconds = [Math]::Round($t.TotalSeconds, 1)
  $script:Text = Get-Content "$S\last.txt" -Raw -Encoding UTF8
  "exit $Code, $Seconds s"
  $Text
}
```

Pass: `yt-dlp --version` prints a version. Record it.

### T1. Labels from a real links file

```powershell
Invoke-Labels "labels `"$LinksFile`""
$LabelsFile = (Get-ChildItem "$env:CLIPPER_LABELS_DIR" -Filter "v*.json" | Select-Object -First 1).FullName
$l = Get-Content $LabelsFile -Raw -Encoding UTF8 | ConvertFrom-Json
$l.source
$l.labels | Format-Table start, end, note -AutoSize -Wrap
```

Pass: exit 0. One `Clip N of M: <title> (<length> s, ends <t>)` line per clip,
in file order, then `Clips read`, `Labels after merging`, `Minutes covered`
and `Saved: <labels folder>\v<VOD number>.json`. The titles and lengths match
the clips on Twitch. `source` is `v<VOD number>.mp4`, the labels are in time
order, each ends at a `t` from the file, and overlapping clips share one
label whose note lists their titles. Record the time, the counts and the
minutes covered.

### T2. An existing labels file needs `--force`

```powershell
$h = (Get-FileHash $LabelsFile).Hash
Invoke-Labels "labels `"$LinksFile`""
"unchanged: " + ((Get-FileHash $LabelsFile).Hash -eq $h)
Invoke-Labels "labels `"$LinksFile`" --force" | Select-Object -Last 1
"same content: " + ((Get-FileHash $LabelsFile).Hash -eq $h)
```

Pass: the first run exits 1 at once with `Error: ... already exists; pass
--force to replace it`, no `Clip` lines, and `unchanged: True`. The run with
`--force` exits 0, and `same content: True`, since the clips did not change.

### T3. Bad links files fail before any lookup (constructed)

```powershell
$first = (Get-Content $LinksFile | Where-Object { $_ -match "^\s*https://" } | Select-Object -First 1)
Set-Content -Path "$S\other-vod.txt" -Encoding utf8 -Value @($first, "https://clips.twitch.tv/MadeUpClip-a https://www.twitch.tv/videos/1?t=5s")
Set-Content -Path "$S\no-t.txt" -Encoding utf8 -Value "https://clips.twitch.tv/MadeUpClip-a https://www.twitch.tv/videos/1"
Invoke-Labels "labels `"$S\other-vod.txt`""
Invoke-Labels "labels `"$S\no-t.txt`""
```

Pass: each exits 1 in a few seconds with one `Error:` line naming the file
and the line: `line 2: VOD v1 differs from v<VOD number> on line 1 ...`, and
`line 1: the VOD URL has no t= ...`. No `Clip` lines.

### T4. An unavailable clip (observe and record)

The clip ID is made up; if it happens to exist, pick another.

```powershell
Set-Content -Path "$S\gone.txt" -Encoding utf8 -Value "https://clips.twitch.tv/ClipperMadeUpClip-zzzzzzzz https://www.twitch.tv/videos/1?t=5s"
Invoke-Labels "labels `"$S\gone.txt`""
"files: " + @(Get-ChildItem "$env:CLIPPER_LABELS_DIR" -Filter "v1.json").Count
```

Pass: exit 1, `Error: yt-dlp failed (exit 1): ...` with yt-dlp's own
`ERROR:` line, no traceback, and `files: 0`. Record the message.

### T5. `eval` against the clip labels

```powershell
Invoke-Labels "eval `"$Analysis`" `"$LabelsFile`""
$env:CLIPPER_TOP_N = "30"
Invoke-Labels "eval `"$Analysis`" `"$LabelsFile`""
Remove-Item Env:CLIPPER_TOP_N
```

Pass: both exit 0 and print the labels and candidates tables, recall and
precision, with `1 video, N labels` matching T1's `Labels after merging`.
Record recall and precision at top 5 and top 30, with the repeats. Precision
is a lower bound: clips are not every moment worth cutting (ADR 0012).

### T6. Clean up

```powershell
Remove-Item Env:CLIPPER_LABELS_DIR, Env:COLUMNS
Get-ChildItem Env:CLIPPER_* -ErrorAction SilentlyContinue   # should print nothing
Remove-Item -Recurse -Force $S
```

## Review page

Covers `clipper review`
([ADR 0024](decisions/0024-review-drafts-in-a-local-browser-page.md), item 3a
amendment). The tests run the server on a free port with a small file, a fake
frame extractor, and no browser. This section checks the page in a real
browser on a real drafts folder, seeking in a multi-GB VOD, the film strip
and handles, Save and Trash, and the errors before the server starts. RV0 to
RV6 write only film strip frames, under `data\cache\frames\` (film strip
amendment); RV7 writes labels and a manifest and deletes a clip file, on a
copy.

### RV0. Setup

You need a folder made by `drafts` (or `render`), with its `manifest.json`,
and its source video in `data\videos\` under the manifest's `source` name.
Chrome or Edge.

```powershell
$Folder = "data\clips\v2917872953"   # any drafts folder
$U = "http://127.0.0.1:8765"
```

### RV1. The page in a browser

```powershell
uv run clipper review $Folder
```

Pass: it prints `Review: http://127.0.0.1:8765/ (N clips; Ctrl+C to stop)`
and opens the page. The page is dark, names the source and the folder's
flags, and lists every clip with its rank, title (or `Clip N`) and span; a
failed clip is red. Clicking a clip jumps the player to its start and shows
its title, hook, span and length. Seeking far ahead (an hour past the clip)
and back plays within a second or two, and Play there plays from that point
with no jump back. Each step button (-15, -5, -1, +1, +5, +15 s) moves the
playhead by that much, playing or paused; -15 s near the start stops at
0:00:00 and +15 s near the end stops at the end. Ctrl+C stops the server.

### RV2. Range requests on the real file

With RV1's server running, in a second terminal (`curl.exe`, not the
PowerShell alias):

```powershell
curl.exe -s -o NUL -D - -H "Range: bytes=0-1023" "$U/video" | Select-String "HTTP|Content-"
curl.exe -s -o NUL -w "%{http_code} %{size_download}B %{time_total}s`n" -H "Range: bytes=-1048576" "$U/video"
curl.exe -s -o NUL -w "%{http_code}`n" -H "Range: bytes=999999999999-" "$U/video"
curl.exe -s -o NUL -w "%{http_code}`n" "$U/nope"
```

Pass: `206 Partial Content`, `Content-Range: bytes 0-1023/<file size>`,
`Content-Length: 1024`, `Content-Type: video/mp4`; the last 1 MB in well under
a second with `206`; `416` past the end; `404` for any other path.

### RV3. Errors before the server starts

With RV1's server still running:

```powershell
uv run clipper review $Folder --no-open; "exit $LASTEXITCODE"
uv run clipper review $Folder --video data\videos\oFbD0Ot7nQ8.mp4 --no-open --port 8766; "exit $LASTEXITCODE"
uv run clipper review data\clips --no-open --port 8766; "exit $LASTEXITCODE"
```

Pass, each exit 1: `Error: Cannot listen on 127.0.0.1:8765: ...` (the port is
taken, and a second server must not share it); `Error: Manifest is for
'<source>' but the video is 'oFbD0Ot7nQ8.mp4'`; `Error: Manifest not found:
data\clips\manifest.json`. Stop RV1's server with Ctrl+C.

If a video whose timestamps do not start at 0 is at hand (check with
`ffprobe -v error -show_entries format=start_time -of csv=p=0 <video>`; the
original download of v2924046310 started at 64 s, but it was replaced by
its remux and deleted), copy it under the name its drafts folder expects, in a scratch
folder, and pass it with `--video`. Pass: exit 1, `Error: ... starts at
64.0 s, not 0, so the page's player would not match the transcript; remux
it: ffmpeg ...`.

### RV4. An AV1 source (observe and record)

YouTube gave AV1 for `oFbD0Ot7nQ8.mp4` (ADR 0020). If a drafts folder exists
for an AV1 source, repeat RV1 and RV5 on it and record whether the browser
plays and seeks it, and how long its first strip takes. Making one needs
`drafts` or `render` on that video; its transcript is cached.

### RV5. The film strip

```powershell
uv run clipper review $Folder
```

Click a clip. Pass: under the step buttons a row of frames appears, each
labelled with its time, covering the clip plus about 2 minutes either side;
the scale reads `<n> s per frame`. The clip's span is outlined in yellow and
the rest dimmed; a white line follows the playhead. The first strip of a clip
fills in within a few seconds (record the time); choosing the clip again, or
reloading the page, shows it at once. Zoom in and Zoom out change the scale
(1, 2, 5, 10, 15, 30, 60 s per frame) around the same centre; ‹ and › move by
half a strip; Fit clip returns to the first view. Clicking a frame seeks the
player there. Stop the server, start `review` on another VOD's folder in
the same browser, and choose a clip: every frame in the strip is from that
VOD, matching the player when you click it (frames are cached by URL, and
each run uses the same address). In a second terminal, `(Get-ChildItem
data\cache\frames\*\*.part.jpg).Count` is 0.

Recorded by the assistant (2026-11-09, `v2917872953`, 17.7 GB H.264, no
browser): one frame takes about 220 ms with ffmpeg; 20 frames fetched 6 at a
time from the server took 1.34 s cold and 0.02 s cached; a second past the
end is `404`.

### RV6. Handles, Set start/end, Preview and Reset

With RV5's page open, on one clip:

1. Drag the start handle left. The player shows the frame under it, and
   Start and Length update; `Edited` appears. Drag it past the strip's left
   edge: the strip moves with it.
2. Drag the end handle right past the clip's end by a few seconds. It cannot
   cross the start handle.
3. Seek with the step buttons, press Set end, then Set start somewhere
   earlier. The handles move there; a time off the strip recentres it. Set
   start after the end shows `The start must be before the end.`
4. Press Preview span. It plays from the start handle and pauses at the end
   handle, within a frame. Press it again and seek during playback: it plays
   on past the end (seeking cancels the preview).
5. A length under 15 s or over 90 s shows in red.
6. Choose another clip and come back: the edit is kept. Reset returns to the
   draft's span. Reloading the page drops unsaved edits.

Ctrl+C stops the server.

### RV7. Save, Trash, title and notes

Trash deletes a clip's file, so use a copy of a drafts folder, and keep the
judged labels away from `data\labels\picks\` unless they are real
judgments. The variable is set in a `cmd` child process only, so Ctrl+C
cannot leave it set in your session:

```powershell
Copy-Item -Recurse $Folder data\clips\rv7-copy
cmd /c "set CLIPPER_LABELS_DIR=data\rv7-labels&& uv run clipper review data\clips\rv7-copy --video data\videos\<source>"
```

1. Choose a clip. The title box holds the model's title (or is empty).
   Change the title, type a note, press Save. Pass: `Saved as post.`, a
   green POST badge in the list, the heading shows the new title.
2. On another clip, move the end handle, press Save. Pass: `Saved as fix;
   re-rendered.` (since item 3d; see RV8) and a yellow FIX badge. Change the
   note: `Unsaved` appears; Save again clears it.
3. Move a clip's start handle into the span of a clip saved in step 1 and
   press Save. Pass: a red message `Overlaps the label ...`; nothing changes.
4. On a third clip press Trash once: it reads `Confirm trash` and goes back
   after 3 s. Press it twice. Pass: `Trashed; the clip's file is deleted.`,
   a TRASHED badge, Trash disabled, and the clip's `.mp4` gone from the
   folder.
5. Reload the page. Pass: badges, titles, saved spans and notes come back.
6. Stop the server. `data\rv7-labels\picks\<stem>.json` has the `post` and
   `fix` labels (the fix with its `draft` span) and the rejected span, with
   the notes; `data\clips\rv7-copy\manifest.json` has `reviewed`, the typed
   title, and no `file` on the trashed clip.

Keep the copy for RV8, then delete `data\clips\rv7-copy` and
`data\rv7-labels`.

### RV8. Re-render on Save

With RV7's copy served again (same `cmd /c` line):

1. On a clip, move the end handle a few seconds later and press Save. Pass:
   Save reads `Rendering…`, both buttons are disabled, the player keeps
   playing; then `Saved as fix; re-rendered.` Record the seconds it took.
   The list shows the new span; in the folder the old `.mp4` is gone and
   one named for the new span is there. Play it: vertical, captions on, and
   it ends where the end handle was.
2. Change only the title and Save. Pass: `Saving…`, done at once, no new
   file.
3. Press Reset. Pass: the handles go back to the draft's span and the title
   box to the model's title (empty if it had none); notes stay; `Unsaved`
   shows. Press Save. Pass: `Saved as post; re-rendered.`; the file is named
   for the draft's span again, and the heading shows the model's title.
4. On the clip trashed in RV7, press Save. Pass: `Saved as post;
   re-rendered.`, a POST badge, and its `.mp4` back in the folder.
5. Stop the server. In `manifest.json` the clip from step 1 has
   `draft_start_seconds`/`draft_end_seconds` (the draft) and
   `start_seconds`/`end_seconds` (its file).

Keep the copy for RV9, then delete `data\clips\rv7-copy` and
`data\rv7-labels`.

### RV9. Add clip

With RV7's copy served again (same `cmd /c` line):

1. Seek to a moment no draft covers and press "Add clip here". Pass: a
   `New clip` appears at the end of the list with a `+` and a NEW badge,
   selected, its handles 30 s apart from the playhead; `Unsaved` shows.
2. Set the handles and a title, press Save. Pass: `Rendering…`, then
   `Saved as added; re-rendered.`; the clip gets the next rank and a blue
   ADDED badge, and a new `.mp4` is in the folder. Reload: it is still
   listed.
3. Move a handle and Save again. Pass: still ADDED; the file is renamed for
   the new span.
4. Add another clip whose span overlaps a saved clip's, and Save. Pass:
   `Overlaps the label ...`; it stays NEW.
5. Trash that unsaved new clip (two clicks). Pass: it leaves the list; no
   request is needed.
6. Trash the added clip from step 2. Pass: it leaves the list and its
   `.mp4` is gone.
7. Stop the server. The labels file has no `added` label left and no
   rejected span for it; `manifest.json` has no entry for it.

Then delete `data\clips\rv7-copy` and `data\rv7-labels`.

### RV10. Draft player and style switch

Covers the style switch ([ADR 0025](decisions/0025-face-crop-style-and-picking-styles.md)).
Make drafts in both styles into a scratch folder and serve them with
scratch labels, so nothing touches your real judgments. The steps use four
clips with both styles, and a clip without a face renders in `fit` only, so
pick a video with a face on screen and check that four clips have a
`.face.mp4`; if not, raise `--top`:

```powershell
cmd /c "set CLIPPER_CLIPS_DIR=data\clips\rv10&& set CLIPPER_OUTPUT_DIR=data\rv10-output&& uv run clipper drafts --no-titles --detector heuristic --top 4 data\videos\<a fetched video>.mp4"
cmd /c "set CLIPPER_LABELS_DIR=data\rv10-labels&& uv run clipper review data\clips\rv10\<its id>"
```

1. Choose clip 1. Pass: a vertical draft plays beside the strip, with `Fit`
   and `Face` buttons, `Fit` pressed.
2. Press `Face`. Pass: the draft switches to the face crop; `Unsaved` shows.
3. Save without moving a handle. Pass: `Saved as post, Face kept.`, no
   rendering; the folder keeps only that clip's `.face.mp4`; the buttons are
   gone (one style left). Reload: the face draft still shows.
4. Choose clip 2, keep `Fit`, move the end handle 5 s, Save. Pass:
   `Rendering…`, then `Saved as fix, Fit kept; re-rendered.`; only the new
   fit `.mp4` is left for that clip.
5. On an unsaved clip with both styles, press `Face`, move a handle, Save.
   Pass: it re-renders in the face style (a few seconds more for faces);
   only the new `.face.mp4` is left.
6. Trash a clip that still has both styles. Pass: both of its files are
   gone.
7. Stop the server. `data\rv10-labels\picks\<id>.json` has `"style"` on each
   saved label (`face`, `fit`, `face`).

Then delete `data\clips\rv10`, `data\rv10-output` and `data\rv10-labels`.

### RV11. Rating and who is on screen

Covers the rating amendment to
[ADR 0024](decisions/0024-review-drafts-in-a-local-browser-page.md). Use any
drafts folder with scratch labels:

```powershell
cmd /c "set CLIPPER_LABELS_DIR=data\rv11-labels&& uv run clipper review data\clips\<a drafts folder>"
```

1. Choose clip 1. Pass: under Notes, `Rating` with `Good` and `OK`, and
   `On screen` with a button per name in `data\people.txt`; none pressed.
2. Press `Good`, then two names. Pass: they show pressed and `Unsaved`
   shows. Press `Good` again: it clears. Press `OK`.
3. Save. Pass: saved as usual; reload the page and choose clip 1: `OK` and
   the two names are still pressed, and `Unsaved` is hidden.
4. Choose clip 2 and Save with nothing pressed. Pass: saved as before.
5. Stop the server. `data\rv11-labels\picks\<id>.json` has `"rating": "ok"`
   and `"people"` with the two names on clip 1's label, and neither on
   clip 2's.

Then delete `data\rv11-labels`. Saving clips here re-renders nothing unless a
handle moved.

## Record your run

| Field | Value |
|-------|-------|
| Date | |
| ffmpeg version | |
| Whisper model(s) | |
| Machine / OS | |
| Source clip length | |

| Step | Result | Notes |
|------|--------|-------|
| F1 | | |
| F2 | | |
| F3 | | |
| F4 | | |
| F5 | | |
| F6 | | |
| F7 | | |
| F8 | | |
| F9 | | |
| W1 | | seconds: |
| W2 | | seconds: |
| W3 | | |
| W4 | | seconds (first tiny run): |
| W5 | | |
| W6 | | |
| W7 | | |
| W8 | | |
| W9 | | words: ; zero-length: ; differs: |
| R0 | | candidates: |
| R1 | | seconds: ; sizes: |
| R2 | | largest duration diff: |
| R3 | | first.png shows: ; last.png shows: |
| R4 | | |
| R5 | | |
| R6 | | |
| R7 | | |
| R8 | | |
| R9 | | |
| R10 | | |
| R11 | | |
| R12 | | |
| R13 | | |
| R14 | | ffmpeg's cause line: |
| R15 | | source size: ; seconds: |
| R16 | | caption size and lines: |
| R17 | | |
| C0 | | temp folders before: |
| C1 | | seconds: |
| C2 | | cues under 0.5 s: |
| C3 | | |
| C4 | | ffmpeg's cause line: |
| C5 | | second drive: |
| C6 | | |
| C7 | | awk4 emoji: ; awk6 backslash: |
| C8 | | |
| L0 | | Ollama version: ; models: |
| L1 | | seconds: ; ollama ps: |
| L2 | | b status and text: ; c, d reason: |
| L3 | | identical per model: |
| L4 | | per model recall / precision / seconds: |
| L5 | | per model seconds / per window: ; top 30: ; top 5: ; ties: |
| L6 | | |

### Reference run

One observed run, for scale. It is not a pass threshold.

- 2026-10-30, Windows 11, ffmpeg `9.0.2-full_build-www.gyan.dev`, `base`
  model on CPU, 30 s speech clip (AV1 video, AAC audio).
- W1 cold: 2.2 s. W2 warm: 0.4 s, from running this checklist as written.
- W4 first `tiny` run, including the model download: 59.6 s. This came from an
  earlier run on the same machine, and depends on the network.
- A 30 s clip produced one candidate, so do not expect five rows from a short
  clip.
- W9, 2026-11-02, by the assistant, with W9's check script on a cold
  `analyze` of the full 5:14 `data\test_input.mp4` (not the 30 s clip): 96
  segments, 721 words, 1 zero-length, none outside its segment, no `differs:`
  lines; 14.7 s cold. The same check on the 5:04:01 stream: 9,343 segments,
  43,058 words, 917 zero-length, none outside, no `differs:` lines; 1,236.1 s
  cold. Measurements are in ADR 0017's word timestamps amendment.

### Rendering reference run

Two runs, for scale. Neither is a pass threshold.

**Earlier by-hand runs**, copied from the maintainer's notes. They were **not**
produced by running R0 to R14 as written, and no render time or file size was
recorded. Windows, PowerShell, `data\test_input.mp4`, a 5:14 speech video.

- `clipper analyze` then `clipper render <video> <json> --rank 1`, and `render`
  with no flag, both succeeded: 5 clips in `data\clips`, named like
  `test_input_000032-000114.mp4`. Every candidate passed the strict duration
  check against real `ffprobe` output.
- `FfmpegClipRenderer` run by hand on one clip: `h264` video and `aac` audio,
  duration 10.01 s for a requested 10.0 s.

**R0 to R14 run as written**, 2026-10-31, Windows 11, PowerShell 5.1, ffmpeg
and ffprobe `9.0.2-full_build-www.gyan.dev`, the same 5:14 video, `base` model
on CPU. Two steps were re-run on their own afterwards because the first
transcript did not capture them (the R3 duration, and the R6 `Test-Path`, which
had been pasted onto the wrong line).

- R0: 5 candidates, from 32.4 to 273.8 s.
- R1: 5 of 5 clips rendered in 12.0 s, 4.7 to 7.3 MB each.
- R2: every clip `h264`, `yuv420p`, `aac`, `video,audio`. Actual duration was
  0.006 to 0.028 s longer than requested, largest 0.028 s.
- R3: first frame shows 12, last frame shows 19, duration 7.52 s for a
  requested 7.5 s.
- R4 to R5: ranks selected, sorted and de-duplicated. Re-run kept the same
  names, and a later rank added a third file.
- R6 to R13: exit 1 with the messages described in each step, and no clips
  folder created. R8 exited 0 and rendered a clip from the wrong video, as
  documented.
- R14: clip 1 rendered. Clip 2 failed with ffmpeg's cause
  `Error opening output <path>: Permission denied`. The summary was
  `Rendered 1 of 2 clips`, the exit code was 1, there was no traceback, and
  the blocking folder remained. The exit number showed as `4294967283`, which
  is `-13` (`EACCES`) as an unsigned 32-bit value: the parked issue in
  `CLAUDE.md`.
- Not covered by this run: the `-v` log (`render` has no `-v`), a missing
  libx264, and captions (see the [Captions](#captions) section).

**R15 to R17**, 2026-11-02, run by the assistant from a script that followed
the steps (its own helpers, a scratch clips folder set for the child process
only), output pasted back. Windows 11, ffmpeg and ffprobe
`9.0.2-full_build-www.gyan.dev`, `data\test_input.mp4` (1280x720) and its saved
analysis.

- R15: ranks 1 and 5 with `--vertical` exited 0 in 8.2 s, each line ending
  ` (vertical)`. Both clips were `h264` 1080x1920, SAR `1:1`, DAR `9:16`,
  `yuv420p`, with `aac` audio, 8.41 and 9.32 MB. Durations 41.375 s and
  30.898 s for requested 41.35 s and 30.87 s (0.025 and 0.028 s over). The
  frame showed the whole source width, undistorted, across the middle, on the
  blurred fill. The source has black bars built into its frame; they showed
  above and below the picture.
- R16: exit 0 in 5.4 s, ` (vertical, captions)`, same streams. The caption
  drew in the blurred area below the picture, large, wrapped to three lines.
- R17 (constructed): 720x1280 and 600x1280 sources both exited 0 with
  1080x1920, SAR `1:1` clips of 6.000 s. The 600x1280 frame showed `5`, the
  picture at full height without visible stretching, and blurred strips at the
  sides.
- An earlier run the same day used a centre crop, which cut off most of the
  frame (the source's burned-in subtitle lost both ends). The maintainer
  rejected it, and the fit replaced it before release (ADR 0016).

**R19**, 2026-11-03, run by the assistant: R0 and R19 extracted from this file
as written and run in a child PowerShell process, with `TEMP` pointed at a
scratch folder and `Invoke-Item` stubbed. Windows 11, ffmpeg and ffprobe
`9.0.2-full_build-www.gyan.dev`, Ollama 0.40.13, `qwen3.5:9b` (default
`CLIPPER_LLM_MODEL`, `100% GPU`), `data\test_input.mp4` analysed afresh by R0
(5 candidates, heuristic).

- a) Exit 0 in 32.5 s for 5 clips with `--vertical --captions --titles`,
  including the model load. Every line ended ` (vertical, captions, titles)`.
  `titles: True`. The titles and hooks, as printed:

  | # | Title (chars) | Hook (chars) |
  |---|---|---|
  | 1 | Why Navy SEALs Inspect Your Bed Every Morning (45) | If you can't do the little things right, you'll never be able to do the big things right. (89) |
  | 2 | Ring the Bell to Quit: The Ultimate Test of Will (48) | Find someone to help you through life and face down bullies until you never give up. (84) |
  | 3 | Be Your Best in the Darkest Moments to Change the World (55) | One person can change the world by giving others hope during their darkest moments. (83) |
  | 4 | It Matters Not Your Background: Changing Lives Happens Everywhere (65) | You don't need to be in uniform or have a specific background to change lives forever. (86) |
  | 5 | Stop Ringing The Bell To Change The World (41) | Ring the bell and stop enduring hardships, but if you want to change the world, never do it. (92) |

  All on one line, no hashtags. Title 4 is over the 60 characters asked for.
  Rank 5's title and hook garble the speaker's point (never ring the bell).
- b) Exit 0 in 16.7 s (`--titles` only). `same titles and hooks: True`.
- c) Exit 1 in 0.5 s. All 5 clips failed with `Ollama at
  http://127.0.0.1:11434 returned HTTP 404 for model
  'no-such-model-clipper:latest': model 'no-such-model-clipper:latest' not
  found. Is the model pulled? (...)`, `clips in the folder: 0`, and every
  manifest entry held that error and no title.
- d) Exit 1 in 2.5 s: `Cannot reach Ollama at http://127.0.0.1:1: [WinError
  10061] No connection could be made because the target machine actively
  refused it. Is it running?`.
- e) Exit 0 in 3.6 s. Rank 1 of the analysis without words got the same title
  and hook as in a).
- f) Exit 0, `Note: #1 0:00:12-0:00:20 has no speech in range; no title`, no
  `Title:` line, and a manifest entry with a `file` and no title or hook.
- `ollama stop` printed terminal control codes on stderr; they are Ollama's
  progress display, not Clipper output.
- Not covered by this run: a long video, another model, and non-ASCII titles.

### Captions reference run

One run of C0 to C8 as written at the time, for scale. It is not a pass
threshold. It was on the v0.2 segment-level SRT captions, before V4 item 4
rewrote these steps for word-by-word ASS captions (ADR 0017). 2026-10-31,
Windows 11, PowerShell, ffmpeg `9.0.2-full_build-www.gyan.dev`, the same 5:14
video (`data\test_input.mp4`) and its R0 analysis (5 candidates). The commands
and their output were pasted back by the maintainer.

- C0: 0 `clipper-captions-*` folders before any run.
- C1: `render --captions` exited 0 and rendered 5 of 5 clips in 12.05 s, 4.79
  to 7.45 MB each, every line ending ` (captions)`. R1 rendered the same
  candidates without captions in 12.0 s, on a different day, so the cost of the
  `subtitles` filter was small. The same render without the flag gave the same
  five file names and no suffix.
- C2: rank 1 had 16 cues, each 2.00 to 3.35 s long. No rank had a cue shorter
  than 0.5 s, so on this video a minimum overlap is not needed. The first cue
  starts at 0.00 s, so there was no `before.png`.
- C3: exit 0. `Note: #1 0:00:12-0:00:20 has no speech in range; rendered
  without captions`, then `Rendered #1` without a suffix and `Rendered #2` with
  ` (captions)`, and `Rendered 2 of 2 clips`.
- C4: 0 folders left after a success and after a failure. The failure gave
  `Rendered 1 of 2 clips`, exit 1, no traceback, and the blocking folder
  remained. ffmpeg's cause was `Error opening output <path>: Permission denied`
  (the exit number showed as `4294967283`, the parked issue in `CLAUDE.md`). The
  `Failed:` message ran to three lines.
- C5: a video path with spaces with a clips folder with spaces, and a relative
  video path with a relative clips folder (`data\clips-captions-manual`), both
  exited 0 with a captioned clip. No folders were left. A second drive was not
  reported, so that part was not run.
- C6: the captioned render changed the file (hash changed, 247061 to 260515
  bytes) and the folder held one file.
- C2, C3, C6, C7 and C8 also have frames to look at. The maintainer reported
  that the frames looked good. Notes for individual frames, such as the emoji
  in `awk4.png` and the backslash line `awk6.png` in C7, were not given.
- This run was on the code before the backslash fix (#54), when `awk6.png`
  was still "observe and record".
- Before this run, the same C7 commands were run on generated media by the
  assistant that wrote the checklist, with `Invoke-Item` stubbed. `awk6.png` showed
  `path C: ew\table` for `path C:\new\table`: libass swallowed the `\n`. The
  emoji drew as a glyph. This was not a run of the full checklist. After the
  fix, the same cue showed `path C:newtable` on the same build.
- C7 re-run by the maintainer on the merged code (after #54): the `awk6.png`
  frame showed `path C:newtable`. The other frames of that re-run were not
  reported.
- Not covered by this run: a build without libass, other ffmpeg builds, and a
  second drive.

### Fetch reference run

One run of D0 to D7 and the clean-up as written at the time, before D8 to
D10 were added. For scale; it is not a pass threshold.
2026-11-03, Windows 11, PowerShell 5.1, yt-dlp 2026.10.26 (a uv tool, put on
`PATH` for the run only), ffmpeg `9.0.2-full_build-www.gyan.dev`. Run by the
assistant: the steps were extracted from this file and run in a child
PowerShell process with `TEMP` pointed at a scratch folder. The videos were
chosen by the maintainer: a 7:54 YouTube talk (`bbz2boNSeL0`) and a 7:32
Twitch VOD (`1691281504`).

- D0: `2026.10.26`.
- D1: exit 0 in 7.5 s. yt-dlp chose format `398+140-20` and merged them;
  `Saved: ...\videos\bbz2boNSeL0.mp4`, 50,834,383 bytes, the only file. AV1
  1280x720 video (the video's highest resolution) and AAC audio, 473.76 s.
  yt-dlp warned that no JavaScript runtime was found and "some formats may be
  missing"; the download worked.
- D2: exit 0 in 53.6 s, format `1080p60`, 46 HLS fragments, then yt-dlp's
  MPEG-TS fix-up. `Saved: ...\videos\v1691281504.mp4`: H.264 1920x1080 and
  AAC, 451.98 s.
- D3: exit 0 in 0.4 s, `Already fetched: ...\bbz2boNSeL0.mp4`,
  `unchanged: True`.
- D4: all four exit 1 in 0.4 s with the messages in the step; `files: 2`.
- D5: exit 1 in 1.5 s: `Error: yt-dlp failed (exit 1):` followed by the
  JavaScript runtime warning and `ERROR: [youtube] zzzzzzzzzz0: This video is
  unavailable`. No file.
- D6: exit 1, `Error: 'yt-dlp' not found on PATH. Install it with ...`.
- D7: exit 0 in 20.1 s (transcription included), 5 candidates from 0.36 to
  0.10, `Saved: ...\out\bbz2boNSeL0.json`.
- Outside the steps, the same YouTube file was analysed and rank 1 rendered
  with `--vertical --captions`: exit 0 in 5.7 s, an H.264 1080x1920 clip of
  41.54 s, and a frame showed the fitted picture on its blurred fill. So an
  AV1 source renders.
- Not covered: a long VOD, a YouTube video above 1080p, a Short, a video that
  is still live, and a run with a JavaScript runtime installed.

**D0 to D11**, 2026-11-03, the same setup and method, with Ollama 0.40.13
and `qwen3.5:9b` for titles. D0 to D7 matched the run above (D2 took 29.7 s
this time). For `drafts`, the maintainer's second YouTube video
(`lCN6FjqDcHg`, 8:53) and the 5:04:01 stream as a local file:

- D8: exit 0 in 71.5 s from the URL: `Saved:` for the video, the table (5
  candidates, 0.59 to 0.15), `Saved:` for the JSON, five lines ending
  ` (vertical, captions, titles)` each with a title and hook, `Manifest:`,
  then `Drafts: ...\clips\lCN6FjqDcHg`. The manifest had all three flags
  true, `analysis` the saved JSON, and every clip a `file`, `captions:
  words`, a title and a hook. Rank 1: H.264 1080x1920, 43.77 s; its frame
  showed the fitted picture on the blurred fill and a three-word caption with
  the active word in yellow. Titles were 43 to 76 characters (two over the
  60 asked for), hooks 73 to 117.
- D9: exit 0 in **1,378 s (22:58)** for the 5:04:01 stream, cold, from a
  local file: 5 candidates (0.85 to 0.80), all 5 rendered vertical,
  captioned and titled. A cold `analyze` of the same stream took 1,260 s
  (vision criterion 1, before word timestamps), so rendering and titles
  added about two minutes. One hook repeated a swear word from the speech.
- D10: with `CLIPPER_LLM_URL` at port 1, exit 1 in 10.8 s: `Already
  fetched`, the cached analysis, a `Failed: #N ...: Cannot reach Ollama at
  http://127.0.0.1:1: [WinError 10061] ...` line per clip, `Rendered 0 of 5
  clips`, then `Drafts:`; `clips: 5` (D8's). With `--no-titles`: exit 0 in
  26.6 s, all 5 rendered ` (vertical, captions)`, no title lines.
- D11: no `CLIPPER_` variable left; the scratch folder was removed.

**D12 and RV10**, 2026-11-10, run by the maintainer. D12 passed. RV10 steps
1 to 4 and 7 passed; 5 and 6 were not reached, since the step then asked
for `--top 2` and so made only two clips, and the maintainer judged that
enough. Two fixes to the steps came out of it: D0 now sets
`CLIPPER_CLIPS_DIR`, which D11 already removed, so D12's drafts no longer go
to `data/clips/`; and RV10 now asks for `--top 4`. The RV10 folder left
behind (`5pk8HLcAAT0`) held two saved clips: rank 1 kept in `face` as
`post`, and rank 2 in `fit` as `fix`, both ends moved 5 s. Its labels file
had `"style"` on both.

### LLM detector reference run

One run, for scale. It is not a pass threshold. 2026-11-02, Windows 11,
PowerShell 5.1, Ollama 0.40.13, RTX 4070 Ti 12 GB (driver 610.88),
`qwen3.5:9b` (6.6 GB) and `llama3.1:8b` (4.9 GB), both `100% GPU`. L1 and L2 a
to c were run by the maintainer; L2 d to L5 were run by the assistant from a
script that followed the steps, with the output pasted back.

- First L1 attempt, before thinking was turned off: `qwen3.5:9b` took 18.75 s
  to 1 m 10 s per window and failed on window 15 of 18 with
  `Model reply for window 0:03:32-0:04:14 is not a valid score: <root>: Invalid
  JSON: EOF while parsing a value at line 1 column 0 (got '')`. Cause and fix
  are in ADR 0014's thinking amendment (#82).
- L1 with `think: false`: exit 0 in 19.5 s including the model load,
  `Scored 18 of 18 windows`, five candidates with one-sentence reasons,
  `Detector: llm:qwen3.5:9b` and the same string in the saved JSON. Scores 0.95,
  0.95, 0.85, 0.85, 0.85.
- L2: a (a placeholder tag) gave HTTP 400 `invalid model name`; b gave HTTP 404
  with the pull hint; c gave `[WinError 10061] No connection could be made
  because the target machine actively refused it` in 2.4 s; d gave the same
  reason in 2.5 s.
- L3: `qwen3.5:9b` saved identical JSON twice. `llama3.1:8b` gave the same
  candidates and scores, but 2 of 6 reasons were worded differently.
- L4 and L5: results and tie counts are in ADR 0014's model-choice amendment.
  On `oFbD0Ot7nQ8.json` at top 30, `qwen3.5:9b` took 953.4 s (0.78 s per
  window) and `llama3.1:8b` 685.2 s (0.56 s per window).
- Not run: L5's second run of the chosen model on the long video.

## If a step fails

| Symptom | Look at |
|---------|---------|
| Duration off by a second or more, or R3 shows 10 (R2, R3) | `-ss` and `-c:v libx264` in `adapters/rendering/ffmpeg.py`; ADR 0007 |
| Wrong streams in a clip (R2) | `-map 0:v:0` and `-map 0:a:0?` in `adapters/rendering/ffmpeg.py`; ADR 0007 |
| Wrong clip name or folder (R1, R5) | `clip_file_name` and `clip_folder` in `core/naming.py`; `Settings.clips_dir`; ADRs 0008 and 0019 |
| Manifest missing, wrong or not replaced (R18) | The manifest block at the end of `render` in `cli/main.py`; `adapters/results/manifest_json.py`; `RenderManifest` in `domain/models.py`; ADR 0019 |
| `--rank` message, order or exit code (R4, R6) | `_select_ranks` in `cli/main.py`; ADR 0011 |
| Bad, missing or empty result file (R7, R11) | `adapters/results/analysis_json.py`; ADR 0009 |
| Overrun or no-video check wrong against real ffprobe (R9, R10) | `_check_media` in `cli/main.py`; `_parse` and `_is_video` in `adapters/media/ffprobe.py`; ADR 0009 |
| Missing-binary message (R13) | `run_tool` in `adapters/media/_process.py`; ADR 0004 |
| One failed clip stops the rest, or the exit code is 0 (R14) | The loop in `render`; ADR 0011 |
| Something was written although the run failed before cutting (R6 to R13) | Check order in `render`; ADR 0011 |
| Vertical clip not 1080x1920, stretched, cut off, or off-centre (R15 to R17) | `_VERTICAL_FILTER` in `adapters/rendering/ffmpeg.py`; ADR 0016 |
| Caption words, highlight or timing wrong (C2) | `build_ass`, `_chunks` and `_word_events` in `core/captions.py`; `-ss` and `-vf` order in `adapters/rendering/ffmpeg.py`; ADR 0017 |
| No note, or a note for a clip with speech (C3) | The `ass` and `from_segments` checks in `render` in `cli/main.py`; ADR 0011, ADR 0017 |
| Caption too big, misplaced or stretched (C8, R16) | `_VERTICAL` and `_SOURCE` in `core/captions.py`; ADR 0017 |
| `c&H`, `\r` or a brace on screen (C7) | `_HIGHLIGHT`, `_RESET` and `_MARKUP` in `core/captions.py`; ADR 0017 |
| Folder left in `%TEMP%` (C4) | `TemporaryDirectory` in `adapters/rendering/ffmpeg.py`; ADR 0010 |
| Caption run fails on a path (C5) | Absolute paths and `cwd` in `adapters/rendering/ffmpeg.py`; `run_tool` in `adapters/media/_process.py`; ADR 0010 |
| Markup, accents or backslashes mangled (C7) | `_MARKUP` in `core/captions.py`; ADR 0010 |
| `Unexpected response` or `not a valid score` on every run (L1) | The payload in `adapters/detection/ollama.py`; `WindowScore` and `_SCHEMA` in `adapters/detection/llm.py`; ADR 0014 |
| Error message wrong or a traceback (L2) | `generate` and `_http_failure` in `adapters/detection/ollama.py`; the `try` in `analyze` and `eval`; ADR 0013 |
| `Detector:` line or saved `detector` wrong (L1) | `_detector_label` in `cli/main.py`; ADR 0013 |
| Timeout on the first window (L1) | `TIMEOUT_SECONDS` in `adapters/detection/ollama.py`; model load time |
| `HTTP 400 ... invalid model name` (L0 to L5) | A placeholder or typo in `$M1` to `$M3` or `CLIPPER_LLM_MODEL`; the L0 `ollama show` check |
| Tens of seconds per window, or `not a valid score ... (got '')` (L1) | `think` in the payload in `adapters/detection/ollama.py`; ADR 0014, thinking amendment |

| Symptom | Look at |
|---------|---------|
| Banner or metadata in the error message (F1, F4) | `-hide_banner -loglevel error` in `ffmpeg.py`; ADR 0004 |
| Generic message for a no-audio file (F3) | `_NO_AUDIO_MARKER` in `ffmpeg.py`; ADR 0005 |
| WAV muxer error on a multi-audio file (F6) | `-map 0:a:0?` in `ffmpeg.py`; ADR 0005 |
| `metadata_errors` TypeError, or odd dependency versions (W1) | `av` and `huggingface-hub` pins; ADR 0003 |
| Cache hit when it should miss, or the reverse (W2, W3, W4) | `CachingTranscriber._cache_path` in `caching.py` |
| `Error:` text mangled or `MarkupError` (F4) | `escape(...)` in `cli/main.py`; ADR 0004 |

Out of scope here: automated versions of these checks (an opt-in model-load
test is on the parked list in `CLAUDE.md`), and the Windows exit-code display.

Also out of scope for the Rendering section:

- A missing libx264 (ADR 0007). It needs a different ffmpeg build.
- The full ffmpeg stderr INFO log for `render`. The command has no `-v`
  (ADR 0011), so only the 10-line message is reachable.

Also out of scope for the Captions section:

- An ffmpeg build without libass (ADR 0010). It needs a different build. Until
  someone runs it, what such a build reports is unknown.
- ffmpeg builds other than the one recorded in your run.
- A BOM or CRLF in the ASS file. The renderer writes the bytes itself, so the
  CLI cannot produce them. A unit test
  (`test_captions_file_is_utf8_without_bom_or_crlf`) checks the bytes handed to
  ffmpeg, but with `subprocess.run` mocked, so it does not show how ffmpeg reads
  a BOM or CRLF.
- Measuring word times against the audio. C2 compares frames with the saved
  transcript, and listening to a few seconds is the only check of the audio.
