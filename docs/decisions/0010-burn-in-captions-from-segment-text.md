# 0010: Burn in captions from segment-level text

- Status: Accepted
- Date: 2026-10-30

## Context

The transcript is segment-level: `FasterWhisperTranscriber` calls
`transcribe(..., vad_filter=True)` without word timestamps, and the transcript
cache stores that shape. Clips are meant to be publishable, and most platforms
ignore soft subtitle tracks.

## Decision

1. A pure function in `core/` turns a `Transcript`, a clip window and nothing
   else into SRT text. Segments overlapping the window are kept and clamped to
   it, and times are shifted so the clip starts at zero. It does no I/O.
2. The renderer writes that text to a temporary `.srt` and burns it in with
   ffmpeg's `subtitles` filter, using one fixed style. The temporary folder is
   removed on success and on failure.
3. Captions are opt-in through a CLI flag.

## Alternatives considered

- **Soft subtitles (a `mov_text` track).** Rejected. They work with stream
  copy, but players and most platforms ignore them, so the clip is not
  publishable.
- **Sidecar `.srt` only.** Rejected for V2. It defers the problem and does not
  produce a finished clip.
- **Word-level captions.** Rejected as out of scope. They need
  `word_timestamps=True`, a `Word` domain model and a change to the cached
  transcript schema, with its own migration question.

## Consequences

- (+) The caption logic is pure and fully unit-testable without ffmpeg.
- (+) The result plays anywhere.
- (−) `base` segments can be long, so a caption block may be large and fill
  the frame.
- (−) Burn-in needs libass in the ffmpeg build. A build without it fails with
  ffmpeg's own message.
- (−) The `subtitles` filter treats `:` and `\` specially, so Windows paths
  such as `C:\Users\...` need escaping. A temp-directory path in a fixed place
  keeps this to one function, which needs a test and a manual step.
- (−) Segment boundaries cut mid-sentence at the clip edges, since text is
  clamped by time and not by words.

## Amended by the caption builder PR (V2 item 9)

- The function is `build_srt(transcript, candidate)` in `core/captions.py`. It
  takes the `ClipCandidate` and reads only its start and end. This refines
  "a clip window and nothing else" above.
- Times are converted to whole milliseconds before clamping and shifting, so
  float noise cannot move a cue. A segment that only touches the clip edge, or
  has no length after rounding, is dropped.
- A segment that straddles an edge keeps its full text and is shown only for
  its in-window part. A short overlap therefore flashes a whole sentence; the
  real-clip check in item 11 decides whether a minimum overlap is needed.
- Whitespace in segment text, newlines included, collapses to single spaces.
  There is no wrapping and no escaping of `{...}`, `<...>` or `-->`. Whether
  ffmpeg needs any is checked with a real build in item 10.
- No overlapping speech returns `""`, a valid SRT with zero cues. It is not an
  error. The caller decides; the intent is to render that clip without
  captions.

## Amended by the burn-in captions PR (V2 item 10)

Checked by hand on 2026-10-31 with ffmpeg 9.0.2 (gyan.dev full build,
`--enable-libass --enable-fontconfig --enable-libfreetype`), Windows 11, on a
constructed 1280x720 test video and on `data/test_input.mp4`. One build only.

- `ClipRenderer.render` takes an optional `captions: str | None` of SRT text.
  `None` or `""` renders without captions. The renderer does not know about
  transcripts; the CLI calls `build_srt` and passes the text.
- The path-escaping function in the Consequences above is not needed. The
  renderer writes `clip.srt` into a temporary folder and runs ffmpeg with that
  folder as its working directory, so the filter is `subtitles=clip.srt:...`
  and no path reaches the filter's escaping rules. The input and output paths
  are made absolute first, because they no longer resolve against the caller's
  directory. `run_tool` gained an optional `cwd` for this. A spaced output path
  worked.
- The file is UTF-8 with no BOM and LF line endings, and the filter passes
  `charenc=UTF-8`. Not tested: a BOM, CRLF, or text outside the Basic
  Multilingual Plane.
- The folder is removed on success and on failure, with
  `ignore_cleanup_errors=True` so a cleanup error cannot mask the real one.
- One fixed style: `force_style='FontSize=18,Outline=2,Shadow=0,MarginV=30'`.
  `FontSize` is relative to libass's 288-pixel SRT canvas, not pixels. These
  values were looked at on two frames and are not tuned.
- Observed on this build:
  - libass wraps a long cue (a 200-character cue took 4 lines and stayed
    inside the frame). `build_srt` does not need to wrap.
  - Timing is correct under input seeking (`-ss` before `-i`) with the filter
    on the output: cues appeared at their clip-relative times.
  - No fontconfig warning and no stall on a first run with no font config.
    The font that rendered looks like Arial; it was not asked for by name.
  - `{...}` is dropped from the text and `<b>...</b>` is rendered as markup.
    In one test, text after a `<` was lost and a following word turned bold; the
    exact rule was not worked out. A lone `<`, a lone `>` and `-->` rendered
    as written.
- So `build_srt` now removes `{`, `}`, `<` and `>` from cue text. There is no
  SRT escape for them. It also costs `-->` its `>` and turns `5 > 3` into
  `5 3`. Whisper rarely produces these characters, and a lost symbol is
  better than lost or restyled words.
- Not verified: what a build without libass reports. This build has it. The
  intent is unchanged: ffmpeg's own stderr surfaces through the usual
  failure message, once per clip.
- Empty captions: when `build_srt` returns `""`, the CLI renders the clip
  without captions and prints a note (see 0011). The renderer treats `""` as
  no captions.
- A captioned clip has the same file name as a plain one (0008), so rendering
  with `--captions` overwrites an earlier plain clip of the same span.

## Amended by the backslash fix (after V2 item 10)

Found on 2026-10-31 by rendering a constructed cue, `path C:\new\table`, with
`--captions` on ffmpeg 9.0.2 (gyan.dev full build), Windows 11. The clip showed
`path C: ew\table`: libass read `\n` as a line break and swallowed it. `\t`
passed through as written. `\N` and `\h` are the other two ASS escapes of this
kind; they were not rendered by hand, since removing the backslash covers all
three.

- `build_srt` now also removes `\` from cue text. Like `{ } < >`, it has no
  escape in SRT. The cost is that `C:\new\table` shows as `C:newtable`. Whisper
  rarely produces a backslash, and a lost symbol is better than a lost word.
- Checked by hand after the change on the same build: the same cue rendered as
  `path C:newtable`. Not checked: any other build.
- Considered and not done: a zero-width space after each backslash, which would
  keep the glyph but depends on the font not drawing a box for it, and
  replacing `\` with a lookalike, which changes the text.

## Revisit when

- Segment-level captions look bad on real clips. That is the case for
  word-level timestamps and its own ADR.

## Related

- 0002: Use faster-whisper for transcription
- 0007: Cut clips by re-encoding
