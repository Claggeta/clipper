# Manual test checklist: ffmpeg and Whisper

CI cannot run the real ffmpeg binary or a real Whisper model. Its tests mock
`subprocess.run` for ffmpeg, use `FakeTranscriber` for the CLI, and the one
integration test decodes a generated WAV through PyAV only. This checklist
covers what is left. It closes the gap recorded in
[ADR 0004](decisions/0004-report-ffmpeg-errors.md) and
[ADR 0005](decisions/0005-classify-no-audio-in-extractor.md): nothing in CI
confirms that `-hide_banner -loglevel error` leaves the cause line on stderr,
or that `does not contain any stream` is still ffmpeg's wording.

## When to run it

- Before tagging a release.
- After upgrading ffmpeg, `faster-whisper`, `av` or `huggingface-hub`
  (see [ADR 0003](decisions/0003-pin-transcription-dependencies.md)).
- After any change to `src/clipper/adapters/media/ffmpeg.py`,
  `src/clipper/adapters/transcription/`, or the ffmpeg command line.

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

### Reference run

One observed run, for scale. It is not a pass threshold.

- 2026-10-30, Windows 11, ffmpeg `9.0.2-full_build-www.gyan.dev`, `base`
  model on CPU, 30 s speech clip (AV1 video, AAC audio).
- W1 cold: 2.2 s. W2 warm: 0.4 s, from running this checklist as written.
- W4 first `tiny` run, including the model download: 59.6 s. This came from an
  earlier run on the same machine, and depends on the network.
- A 30 s clip produced one candidate, so do not expect five rows from a short
  clip.

## If a step fails

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
