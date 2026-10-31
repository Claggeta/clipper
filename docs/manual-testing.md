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

## When to run it

- Before tagging a release.
- After upgrading ffmpeg, `faster-whisper`, `av` or `huggingface-hub`
  (see [ADR 0003](decisions/0003-pin-transcription-dependencies.md)).
- After any change to `src/clipper/adapters/media/ffmpeg.py`,
  `src/clipper/adapters/transcription/`, or the ffmpeg command line.
- After any change to `src/clipper/adapters/rendering/`,
  `src/clipper/adapters/media/ffprobe.py`, `src/clipper/adapters/results/`,
  or the `render` command. Run the [Rendering](#rendering) section.

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
  libx264, and captions.

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
- Captions (`--captions`). They arrive with V2 items 9 to 11, with their own
  checklist steps.
