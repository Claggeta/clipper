# Manual test checklist: ffmpeg, Whisper and rendering

CI cannot run the real ffmpeg binary or a real Whisper model. Its tests mock
`subprocess.run` for ffmpeg, use `FakeTranscriber` for the CLI, and the one
integration test decodes a generated WAV through PyAV only. This checklist
covers what is left. It closes the gap recorded in
[ADR 0004](decisions/0004-report-ffmpeg-errors.md) and
[ADR 0005](decisions/0005-classify-no-audio-in-extractor.md): nothing in CI
confirms that `-hide_banner -loglevel error` leaves the cause line on stderr,
or that `does not contain any stream` is still ffmpeg's wording.

The [Rendering](#rendering) section covers `clipper render` the same way. The
render tests patch `_build_renderer` and `_build_probe` (ADR 0011), so nothing
in CI runs the real `ffprobe` JSON parsing or the real clip encode.

The [Captions](#captions) section covers `clipper render --captions`. The
renderer tests mock `subprocess.run`, so nothing in CI runs the real `subtitles`
filter.

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

## Rendering

Covers `clipper render` ([ADR 0007](decisions/0007-cut-clips-by-re-encoding.md),
[0008](decisions/0008-clip-output-location-and-naming.md),
[0009](decisions/0009-render-reads-saved-json-and-probes-media.md),
[0011](decisions/0011-render-command-shape-and-failure-policy.md)). It is
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
    Get-ChildItem $env:CLIPPER_CLIPS_DIR | Select-Object Name, Length | Format-Table -AutoSize
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
- A last line `Rendered X of X clips to <clips folder>`, with `X` equal to the
  candidate count.
- The clips folder holds `X` files named `<stem>_HHMMSS-HHMMSS.mp4`. In each
  name the start is the candidate's start rounded **down** and the end is its
  end rounded **up** to whole seconds (ADR 0008).

The console line prints both ends rounded **down**, while the file name rounds
the end **up**. A candidate ending at 73.8 s prints `0:01:13` but is named
`...-000114.mp4`, so the two can differ by one second. That is not a failure.

Record the seconds, which is the first render time measured for this tool
(ADR 0007 notes none exists), and the file sizes.

### R2. Rendered clips are H.264, AAC, yuv420p, and the right length

Run after R1, without resetting the clips folder.

```powershell
$cands = $r.candidates | Sort-Object start_seconds
$files = Get-ChildItem $env:CLIPPER_CLIPS_DIR -Filter "*.mp4" | Sort-Object Name
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
$Clip = "$env:CLIPPER_CLIPS_DIR\testsrc_000012-000020.mp4"
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
the flags were given. The last line is `Rendered 2 of 2 clips`. The clips folder
holds two files, the ones for candidates 1 and 3 from R0.

### R5. Re-running is idempotent, and old clips are left alone

Run after R4, without resetting the clips folder.

```powershell
$before = Get-ChildItem $env:CLIPPER_CLIPS_DIR | Select-Object -ExpandProperty Name
Invoke-Render $Src $Json -Rank 1,3 | Out-Null
$Code
$after = Get-ChildItem $env:CLIPPER_CLIPS_DIR | Select-Object -ExpandProperty Name
"same names: " + (-not (Compare-Object $before $after))
Invoke-Render $Src $Json -Rank 2 | Out-Null
$Code
@(Get-ChildItem $env:CLIPPER_CLIPS_DIR).Count
```

Pass: exit 0 both times. `same names: True`, with no numbered or suffixed
copies. After rendering rank 2 the folder holds 3 files, so the files from R4
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
New-Item -ItemType Directory -Force "$env:CLIPPER_CLIPS_DIR\testsrc_000025-000035.mp4" | Out-Null
Invoke-Render "$S\testsrc.mp4" "$S\testsrc-two.json"
Show-Clips
Test-Path "$env:CLIPPER_CLIPS_DIR\testsrc_000025-000035.mp4" -PathType Container
```

Pass:

- A `Rendered #1 ...` line for `testsrc_000012-000020.mp4`, and that file exists
  and is not empty.
- A `Failed: #2 0:00:25-0:00:35: ffmpeg failed (exit ...): ...` line. Record the
  cause ffmpeg gave. I have not seen it.
- The last line is `Rendered 1 of 2 clips to <clips folder>`, and the exit
  code is 1 (ADR 0011).
- No traceback. The renderer tries to delete the failed output and cannot
  delete a folder, which must be logged and not raised, so the last command
  prints `True`.

The exit number may show as an unsigned 32-bit value on Windows. That is the
parked issue in `CLAUDE.md`. Do not fail the step on it. If ffmpeg unexpectedly
succeeds here, record that and the contents of the folder.

## Captions

Covers `clipper render --captions` ([ADR 0010](decisions/0010-burn-in-captions-from-segment-text.md),
[0011](decisions/0011-render-command-shape-and-failure-policy.md)). Run the
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
  if (Test-Path $CapClips) { Get-ChildItem $CapClips | Select-Object Name, Length | Format-Table -AutoSize }
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

# Helper: the transcript segments that overlap a candidate, with clip-relative
# times. This is what the SRT should contain.
function Get-Cues($Res, [int]$Rank) {
  $c = $Res.candidates[$Rank - 1]
  foreach ($s in $Res.transcript.segments) {
    # [double] casts: JSON whole numbers are integers, and [Math]::Max(10, 12.5)
    # would pick the integer overload and return 12.
    $a = [Math]::Max([double]$s.start_seconds, [double]$c.start_seconds)
    $b = [Math]::Min([double]$s.end_seconds, [double]$c.end_seconds)
    if ($b -gt $a) {
      [pscustomobject]@{ Start = $a - $c.start_seconds; End = $b - $c.start_seconds; Text = $s.text.Trim() }
    }
  }
}

# Helper (constructed fixtures): a result JSON with a transcript.
# Spans are "start-end"; Segments are "start|end|text". Written as UTF-8
# without a BOM, which is what the JSON loader expects.
function New-CapResult {
  param([string]$Path, [string]$Source, [string[]]$Spans, [string[]]$Segments)
  $cands = @(foreach ($sp in $Spans) {
    $a, $b = $sp -split "-"
    @{ start_seconds = [double]$a; end_seconds = [double]$b; score = 0.5; reasons = @("constructed") }
  })
  $segs = @(foreach ($sg in $Segments) {
    $a, $b, $t = $sg -split "\|", 3
    @{ start_seconds = [double]$a; end_seconds = [double]$b; text = $t }
  })
  $doc = @{ source = $Source; transcript = @{ segments = $segs }; candidates = $cands }
  [IO.File]::WriteAllText($Path, (ConvertTo-Json -InputObject $doc -Depth 6),
    (New-Object Text.UTF8Encoding $false))
}

# Constructed: testsrc.mp4 (from R0), two candidates (12.5-20 and 25-35).
# Segment 10-12.5 only touches the first clip's start, 21-24 is between the
# clips, so the first clip has no speech. The second clip has one segment.
New-CapResult "$S\cap-nospeech.json" "testsrc.mp4" @("12.5-20", "25-35") @(
  "0|5|far before both clips",
  "10|12.5|touches the start edge of clip one",
  "21|24|between the clips",
  "26|33|words inside the second clip")

# Constructed: one candidate, six segments of awkward text (C7).
$e  = [string][char]0x00E9           # e with acute
$i  = [string][char]0x00EF           # i with diaeresis
$em = [char]::ConvertFromUtf32(0x1F600)   # outside the Basic Multilingual Plane
New-CapResult "$S\cap-awkward.json" "testsrc.mp4" @("12.5-20") @(
  "12.5|13.75|braces {a} and <b>bold</b> end",
  "13.75|15|arrow --> and 5 > 3",
  "15|16.25|caf$e na${i}ve",
  "16.25|17.5|before $em after",
  "17.5|18.75|it's 100% `"fine`" & ok",
  "18.75|20|path C:\new\table")

# Constructed: a 1280x720 source with the same name as testsrc.mp4 (C8).
New-Item -ItemType Directory -Force "$S\hd" | Out-Null
ffmpeg -hide_banner -loglevel error -y -f lavfi -i "testsrc=size=1280x720:rate=25:duration=40" `
  -f lavfi -i "sine=frequency=440:duration=40" -shortest `
  -c:v libx264 -pix_fmt yuv420p -c:a aac "$S\hd\testsrc.mp4"

# Constructed: a result for testsrc.mp4 with speech in both clips, for C4 to C6.
New-CapResult "$S\cap-testsrc.json" "testsrc.mp4" @("12.5-20", "25-35") @(
  "13|19|first clip speech",
  "26|34|second clip speech")

"captions temp folders before any run: " + (Get-CaptionTempCount)
```

Pass: the `ffmpeg` command prints nothing, and the last line prints a number.
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
  that has no speech in range. Record any.
- Without the flag the same lines have no suffix, and the file names are the
  same (ADR 0008).
- The last line is `Rendered X of X clips to <clips folder>` in both runs.

Record the seconds. R1 gave the plain render time for the same candidates, so
the difference is the cost of the `subtitles` filter.

### C2. Caption text and timing match the transcript

Compares the burned-in text with the saved transcript, on the real clip with
the highest rank. The source video may have text of its own in the picture. If
it does, compare against the transcript and not against that text.

```powershell
Reset-CapClips
Invoke-RenderC $Src $Json $CapClips -Rank 1 -Captions
$Clip = (Get-ChildItem $CapClips -Filter "*.mp4" | Select-Object -First 1).FullName
$cues = @(Get-Cues $r 1)
$cues | Format-Table @{n="Start";e={"{0:N2}" -f $_.Start}}, @{n="End";e={"{0:N2}" -f $_.End}}, `
  @{n="Len";e={"{0:N2}" -f ($_.End - $_.Start)}}, Text -AutoSize -Wrap
$pick = @($cues[0], $cues[[int](($cues.Count - 1) / 2)], $cues[-1])
$n = 0
foreach ($c in $pick) {
  $n++
  Save-Frame $Clip (($c.Start + $c.End) / 2) "$S\cue$n.png"
  "cue$n.png : middle of '" + $c.Text + "'"
}
Remove-Item "$S\before.png" -ErrorAction SilentlyContinue
if ($cues[0].Start -gt 0.5) { Save-Frame $Clip ($cues[0].Start / 2) "$S\before.png"; "before.png : before the first cue" }
1..3 | ForEach-Object { Invoke-Item "$S\cue$_.png" }
if (Test-Path "$S\before.png") { Invoke-Item "$S\before.png" }
```

Pass:

- Each `cueN.png` shows the text printed on its line, perhaps wrapped over
  several lines, inside the frame, near the bottom.
- `before.png`, if made, shows no caption.
- The text has none of `{ } < >` in it.

Then list the short cues in all candidates. A segment that crosses the edge of a
clip keeps its full text for the part inside the clip, so a short overlap
flashes a whole sentence (ADR 0010):

```powershell
1..$r.candidates.Count | ForEach-Object {
  $k = $_
  Get-Cues $r $k | Where-Object { ($_.End - $_.Start) -lt 0.5 } | ForEach-Object {
    "rank {0}: {1:N2}-{2:N2} ({3:N2} s) {4}" -f $k, $_.Start, $_.End, ($_.End - $_.Start), $_.Text
  }
}
```

Observe and record: how many cues are shorter than 0.5 s, and whether you
would want a minimum overlap. ADR 0010 left that decision to this step. Do not
fail the step on it.

### C3. A clip with no speech renders plain, with a note (constructed)

Uses `cap-nospeech.json`. Clip 1 (12.5 to 20 s) has no speech in range.
A segment that ends exactly at 12.5 s must not count. Clip 2 (25 to 35 s) has
one segment.

```powershell
Reset-CapClips
Invoke-RenderC "$S\testsrc.mp4" "$S\cap-nospeech.json" $CapClips -Captions
Show-CapClips
Save-Frame "$CapClips\testsrc_000012-000020.mp4" 3 "$S\nospeech-1.png"
Save-Frame "$CapClips\testsrc_000025-000035.mp4" 4.5 "$S\nospeech-2.png"
Invoke-Item "$S\nospeech-1.png"; Invoke-Item "$S\nospeech-2.png"
```

Pass:

- Exit 0.
- A line `Note: #1 0:00:12-0:00:20 has no speech in range; rendered without
  captions`, before the `Rendered #1` line, and the `Rendered #1` line has no
  ` (captions)`.
- `Rendered #2 0:00:25-0:00:35 <path> (captions)`.
- The last line is `Rendered 2 of 2 clips to <clips folder>`.
- `nospeech-1.png` shows no caption. `nospeech-2.png` shows `words inside the
  second clip`.

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
New-Item -ItemType Directory -Force "$CapClips\testsrc_000025-000035.mp4" | Out-Null
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
Get-ChildItem "$S\clips with space" | Select-Object Name, Length

# b) a relative video path and a relative clips folder
$RelVideo = Resolve-Path -Relative "$S\testsrc.mp4"
$RelClips = ".\data\clips-captions-manual"
Remove-Item -Recurse -Force $RelClips -ErrorAction SilentlyContinue
"relative video: $RelVideo"
Invoke-RenderC $RelVideo "$S\cap-testsrc.json" $RelClips -Rank 1 -Captions
Get-ChildItem $RelClips | Select-Object Name, Length
Remove-Item -Recurse -Force $RelClips

"folders left: " + (Get-CaptionTempCount)
```

Pass: for both a) and b), exit 0, a `Rendered #1 ... (captions)` line, and a
non-empty `testsrc_000012-000020.mp4`. In b) the clip is in the repo's
`data\clips-captions-manual` and not in the temporary folder. The step removes
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
$f = "$CapClips\testsrc_000025-000035.mp4"
$h1 = (Get-FileHash $f).Hash; $s1 = (Get-Item $f).Length
Save-Frame $f 4.5 "$S\over-1.png"

Invoke-RenderC "$S\testsrc.mp4" "$S\cap-testsrc.json" $CapClips -Rank 2 -Captions | Out-Null
"captioned: exit $Code, hash changed: " + ((Get-FileHash $f).Hash -ne $h1) + ", size $s1 -> " + (Get-Item $f).Length
Save-Frame $f 4.5 "$S\over-2.png"

Invoke-RenderC "$S\testsrc.mp4" "$S\cap-testsrc.json" $CapClips -Rank 2 | Out-Null
"plain again: exit $Code"
Save-Frame $f 4.5 "$S\over-3.png"
"files: " + @(Get-ChildItem $CapClips).Count
1..3 | ForEach-Object { Invoke-Item "$S\over-$_.png" }
```

Pass: `hash changed: True`, one file in the folder (no numbered or suffixed
copies), `over-1.png` and `over-3.png` show no caption, and `over-2.png` shows
`second clip speech`.

### C7. Awkward text (constructed)

Uses `cap-awkward.json`. `build_srt` removes `{ } < >` (ADR 0010), so the
expected text on screen is below. The emoji is outside the Basic Multilingual
Plane, and the last line has backslashes. Neither was checked before this step.

```powershell
Reset-CapClips
Invoke-RenderC "$S\testsrc.mp4" "$S\cap-awkward.json" $CapClips -Captions
$f = "$CapClips\testsrc_000012-000020.mp4"
$mid = 0.625, 1.875, 3.125, 4.375, 5.625, 6.875
for ($k = 0; $k -lt 6; $k++) { Save-Frame $f $mid[$k] "$S\awk$($k + 1).png"; Invoke-Item "$S\awk$($k + 1).png" }
```

| Frame | Expected on screen |
|-------|--------------------|
| `awk1.png` | `braces a and bbold/b end` |
| `awk2.png` | `arrow -- and 5 3` |
| `awk3.png` | the word `café` and the word `naïve`, with their accents |
| `awk4.png` | `before` and `after`. Observe and record the emoji |
| `awk5.png` | `it's 100% "fine" & ok` |
| `awk6.png` | observe and record: `path C:\new\table` as written, or a line break at `\n` |

Pass: exit 0 and all six frames show their text. The words around the emoji in
`awk4.png` must be present. The emoji itself may be missing or a box, because
the font that rendered in ADR 0010 looks like Arial. Record what you saw.

Observe and record `awk6.png`. If `\new` turned into a line break, libass read
`\n` as markup. That is a new finding and needs an ADR 0010 amendment and a
code change. Do not fold it into a release.

### C8. How it looks (observe and record)

The style is `FontSize=18,Outline=2,Shadow=0,MarginV=30`, looked at on two
frames in ADR 0010 and not tuned. This step records what it looks like at two
sizes. It has no pass line. Use the constructed 640x360 `testsrc.mp4` and the
1280x720 copy from C0.

```powershell
Reset-CapClips
Invoke-RenderC "$S\testsrc.mp4" "$S\cap-awkward.json" $CapClips -Captions | Out-Null
$sd = "$CapClips\testsrc_000012-000020.mp4"
Copy-Item $sd "$S\style-640.mp4"
Invoke-RenderC "$S\hd\testsrc.mp4" "$S\cap-awkward.json" $CapClips -Captions | Out-Null
Copy-Item $sd "$S\style-1280.mp4"
foreach ($w in 640, 1280) {
  Save-Frame "$S\style-$w.mp4" 0.625 "$S\style-$w-a.png"
  Save-Frame "$S\style-$w.mp4" 3.125 "$S\style-$w-b.png"
  Invoke-Item "$S\style-$w-a.png"; Invoke-Item "$S\style-$w-b.png"
}
```

Record: is the text readable on the test picture, how big it is next to the
frame at both sizes, and whether any line touches the frame edge. Fail the
step only if text is cut off by the frame. Size and placement are a judgement
for the maintainer, and a change to them is a code change, not part of a
release.

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
| C0 | | temp folders before: |
| C1 | | seconds: |
| C2 | | cues under 0.5 s: |
| C3 | | |
| C4 | | ffmpeg's cause line: |
| C5 | | second drive: |
| C6 | | |
| C7 | | awk4 emoji: ; awk6 backslash: |
| C8 | | |

### Reference run

One observed run, for scale. It is not a pass threshold.

- 2026-10-30, Windows 11, ffmpeg `9.0.2-full_build-www.gyan.dev`, `base`
  model on CPU, 30 s speech clip (AV1 video, AAC audio).
- W1 cold: 2.2 s. W2 warm: 0.4 s, from running this checklist as written.
- W4 first `tiny` run, including the model download: 59.6 s. This came from an
  earlier run on the same machine, and depends on the network.
- A 30 s clip produced one candidate, so do not expect five rows from a short
  clip.

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

### Captions reference run

One run of C0 to C8 as written, for scale. It is not a pass threshold.
2026-10-31, Windows 11, PowerShell, ffmpeg `9.0.2-full_build-www.gyan.dev`, the
same 5:14 video (`data\test_input.mp4`) and its R0 analysis (5 candidates).
The commands and their output were pasted back by the
maintainer.

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
- Before this run, the same C7 commands were run on generated media by the
  assistant that wrote the checklist, with `Invoke-Item` stubbed. `awk6.png` showed
  `path C: ew\table` for `path C:\new\table`: libass swallowed the `\n`. The
  emoji drew as a glyph. This was not a run of the full checklist.
- Not covered by this run: a build without libass, other ffmpeg builds, and a
  second drive.

## If a step fails

| Symptom | Look at |
|---------|---------|
| Duration off by a second or more, or R3 shows 10 (R2, R3) | `-ss` and `-c:v libx264` in `adapters/rendering/ffmpeg.py`; ADR 0007 |
| Wrong streams in a clip (R2) | `-map 0:v:0` and `-map 0:a:0?` in `adapters/rendering/ffmpeg.py`; ADR 0007 |
| Wrong clip name or folder (R1, R5) | `core/naming.py`; `Settings.clips_dir`; ADR 0008 |
| `--rank` message, order or exit code (R4, R6) | `_select_ranks` in `cli/main.py`; ADR 0011 |
| Bad, missing or empty result file (R7, R11) | `adapters/results/analysis_json.py`; ADR 0009 |
| Overrun or no-video check wrong against real ffprobe (R9, R10) | `_check_media` in `cli/main.py`; `_parse` and `_is_video` in `adapters/media/ffprobe.py`; ADR 0009 |
| Missing-binary message (R13) | `run_tool` in `adapters/media/_process.py`; ADR 0004 |
| One failed clip stops the rest, or the exit code is 0 (R14) | The loop in `render`; ADR 0011 |
| Something was written although the run failed before cutting (R6 to R13) | Check order in `render`; ADR 0011 |
| Caption text or timing wrong (C2) | `build_srt` in `core/captions.py`; `-ss` and `-vf` order in `adapters/rendering/ffmpeg.py`; ADR 0010 |
| No note, or a note for a clip with speech (C3) | The `srt` check in `render` in `cli/main.py`; ADR 0011 |
| Folder left in `%TEMP%` (C4) | `TemporaryDirectory` in `adapters/rendering/ffmpeg.py`; ADR 0010 |
| Caption run fails on a path (C5) | Absolute paths and `cwd` in `adapters/rendering/ffmpeg.py`; `run_tool` in `adapters/media/_process.py`; ADR 0010 |
| Markup, accents or backslashes mangled (C7) | `_MARKUP` in `core/captions.py`; ADR 0010 |

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
- A BOM or CRLF in the SRT file. The renderer writes the bytes itself, so the
  CLI cannot produce them. A unit test
  (`test_captions_file_is_utf8_without_bom_or_crlf`) checks the bytes handed to
  ffmpeg, but with `subprocess.run` mocked, so it does not show how ffmpeg reads
  a BOM or CRLF.
- Word-level captions and caption styling (ADR 0010, non-goals for V2).
