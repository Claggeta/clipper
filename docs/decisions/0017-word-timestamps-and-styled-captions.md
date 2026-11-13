# 0017: Word timestamps and styled captions

- Status: Accepted
- Date: 2026-11-02
- Supersedes: 0010's segment-level decision (decision 1, and the
  "word-level captions" alternative). 0010's burn-in mechanism stands.

## Context

Captions today are whole Whisper segments in one small style (0010). On a
vertical clip (0016) a segment fills the frame, and short-form captions are
usually a few words at a time, large, with the spoken word highlighted. The
maintainer needs styled captions before daily use (V4 scope). That needs the
time of each word, which the transcript does not hold:
`FasterWhisperTranscriber` calls `transcribe(..., vad_filter=True)` without
word timestamps.

## Decision

1. **Domain.** A new `Word` model (`start_seconds`, `end_seconds`, `text`) and
   a `words: tuple[Word, ...] = ()` field on `Segment`. The field has a
   default, so older `AnalysisResult` JSON and cache entries still load, with
   no words.
2. **Transcription.** `FasterWhisperTranscriber` passes `word_timestamps=True`.
   Its `name` changes (a `+words` suffix), which changes the transcript cache's
   namespace, so old cache entries are misses and are re-transcribed rather
   than migrated.
3. **Captions.** A pure function in `core/` builds ASS subtitle text from the
   transcript and a candidate, and replaces `build_srt`. It shows short chunks
   of words (at most about three, set in the implementing PR) in one bold
   style, with the word being spoken highlighted. Each word is its own ASS
   event showing its chunk, so no karaoke tags are needed. It keeps 0010's
   rules: times in whole milliseconds, clamped and shifted to the clip, and
   `{ } < > \` removed from text.
4. **Old transcripts.** A segment with no words becomes one cue with its whole
   text, in the same style, from the same function. `render` prints a note when
   a clip's captions came from segments without words. There is one builder
   and one filter path.
5. **Burn-in** is unchanged from 0010: a temporary folder, the `subtitles`
   filter with a relative file name, cleanup on success and failure. Style
   values (font size, outline, colours, position) are chosen in the
   implementing PR from real frames, for both the source frame and 0016's
   vertical frame, and recorded there as not tuned.

## Alternatives considered

- **Keep segment captions and only restyle them.** Rejected. Segments are long,
  and a large font makes them worse.
- **Split segment text evenly over the segment's time.** No transcription
  change, but words drift from speech. Rejected.
- **Karaoke tags (`\k`) in one event per chunk.** Fewer events, but libass's
  handling would need its own checks. Not chosen.
- **Migrate old cache entries in place.** Rejected. The words are not in them;
  only re-transcription can add them.
- **Keep `build_srt` beside the new builder.** Rejected. Two caption paths and
  two sets of manual steps.

## Consequences

- (+) Captions can follow speech word by word.
- (+) The domain change is additive, so v0.3 JSON still loads in `render` and
  `eval`.
- (-) Every video is transcribed again once. The 5:04:01 stream took 20:59 on
  CPU without word timestamps.
- (-) What `word_timestamps=True` costs in time, and how accurate its times are
  on this machine, is unverified. The implementing PR measures it.
- (-) Saved JSON grows: every word carries two times.
- (-) Burn-in still needs libass (0010), and ASS gives more ways to get the
  markup wrong than SRT. The manual steps cover awkward text again.
- (-) The SRT manual steps (C2, C7) are rewritten for ASS.

## Amended by the word timestamps PR (V4 item 3)

Decisions 1 and 2 are done. Captions (decisions 3 to 5) are item 4.

- `Word` is frozen, with `start_seconds` (at least 0), `end_seconds` (not
  before the start; zero length is allowed) and `text`. `Segment` validates
  that its words are in order, do not overlap and lie inside it. Word
  probability is not stored.
- `build_transcript` takes `(start, end, text, words)` and cleans words the way
  it cleans segments: text stripped, empty words dropped, times clamped into
  the segment (including a start the overlap rule pushed later) and kept in
  order. A word left with no time keeps zero length.
- **Split words are merged.** faster-whisper starts every real word with a
  space. Its default prepend punctuation (which includes `-` and `'`) makes a
  token without one: on `test_input.mp4` it returned `' battle', '-hardened'`
  and `' o', "'clock."`. Stripping lost the only sign that these belong
  together, so a token without a leading space is merged into the word before
  it in the same segment, which then ends where the token ends. A first token
  without a space stays a word.
- **Segment boundaries change.** faster-whisper 1.2.1 sets each segment's start
  and end to its first and last word's times when word timestamps are on
  (`transcribe.py`, read in the installed package). The decode also changes
  slightly, so text and splits differ from a transcript made without words.

Measured on 2026-11-02, Windows 11, Whisper `base`, CPU, int8, cold runs into
a scratch output folder:

| | `test_input.mp4` (5:14) | `oFbD0Ot7nQ8.mp4` (5:04:01) |
|---|---|---|
| Cold `analyze`, without words (earlier runs) | 14.3 s | 1,260 s |
| Cold `analyze`, with words | 14.7 s | 1,236.1 s |
| Saved JSON | 14,911 to 110,243 bytes | 1,230,241 to 7,171,013 bytes |
| Segments | 97 to 96 | 9,420 to 9,343 |
| Text | 3,800 to 3,799 characters | 225,284 to 219,212 characters |
| Words; zero-length | 721; 1 | 43,058; 917 |
| Segments without words; words outside their segment; joined words differing from the text | 0; 0; 0 | 0; 0; 0 |
| Heuristic `eval`, top 5 | recall 3/4, precision 3/5 (unchanged) | recall 1/30, precision 1/5 (was 2/30, 2/5) |
| Heuristic `eval`, top 30 | - | recall 6/30, precision 6/30, 2 repeats (was 5/30, 5/30) |

- Word timestamps cost no measurable time on this machine; each video was
  timed once.
- On the short video about a dozen segments were split differently, and some
  words changed both ways: "the perfection" became "to perfection", and
  "Iraq and Afghanistan" became "a rack in Afghanistan". Neither transcript is
  consistently better.
- The heuristic's candidates moved with the segments. The long video's `eval`
  numbers changed by one label each way. That is the transcript, not the
  detector, and with 30 labels it is within what these indicative numbers can
  show (0012).
- Not measured: how accurate the word times are against the audio. Item 4's
  frames are the first check.

## Amended by the styled captions PR (V4 item 4)

Decisions 3 to 5 are done.

- **Builder.** `build_ass(transcript, candidate, *, vertical)` in
  `core/captions.py` replaces `build_srt`. It returns `Captions(text,
  from_segments)`: the ASS file, or `""` when no event remains, and whether
  any cue came from a segment without words. ADR 0011's mention of
  `build_srt` now means this function.
- **Words in a clip.** A word is kept if its start is inside the clip,
  `[start, end)`, so zero-length words count. Its end is clamped to the clip.
  A word that starts before the clip's first frame is not shown, even if it
  is still being spoken.
- **Chunks.** At most three words (`_WORDS_PER_CHUNK`). A chunk also ends at
  the end of a segment and after a word ending in `.`, `?` or `!`, and the
  first word in a clip starts a chunk.
- **Events.** Each word is one `Dialogue` showing its chunk, with that word
  coloured by an inline `{\c&H0000FFFF&}...{\r}` (yellow, then back to the
  style). A word's event lasts until the next word in its chunk starts, so
  the chunk does not flicker between words. The chunk's last word ends with
  the word, so the screen is blank in a pause between chunks. There is no
  hold between chunks and no character cap per chunk. Both were left out
  until real clips show a need.
- **Times.** Whole milliseconds, as before, then written in ASS's
  `H:MM:SS.cc`, rounded half up to centiseconds. An event with no length
  after that is dropped, so a zero-length word appears in its chunk but is
  never highlighted.
- **Layouts.** `vertical=True` writes `PlayResX/Y` 1080x1920, 0016's frame.
  Otherwise the canvas is 1920x1080 and libass scales it to the source frame.
  `MediaInfo` was not given the frame size (an ADR 0009 change). On the build
  below, text on a 600x1280 and a 640x480 constructed source was not
  stretched. It seems to scale with the frame's height: on 600x1280 a
  three-word chunk wrapped to two lines and stayed inside the frame.
- **Style.** One `Default` style in the file: Arial, bold, white, a black
  outline, no shadow, bottom centre. Vertical: size 80, outline 6, side
  margins 60, `MarginV` 420, which puts the text in the blurred fill below a
  16:9 picture. Source frame: size 72, outline 5, side margins 80, `MarginV`
  80. The maintainer chose both from frames of rank 1 of `test_input.mp4`,
  comparing Arial, Arial Black and Impact, and text over the picture with
  text in the fill. Not tuned beyond that one video.
- **Renderer.** It writes `clip.ass`, and the filter is
  `subtitles=clip.ass:charenc=UTF-8`, with no `force_style`, since the style
  is in the file. The temporary folder, the bare file name and `cwd`, the
  vertical filter first, and the cleanup are unchanged (decision 5).
- **Old analyses.** A segment without words is one cue with its whole text,
  clamped as in 0010. When any such cue is in a clip, `render` prints
  `Note: #N <span> has no word timings; captions show whole segments`.
- **Unchanged from 0010:** `{ } < > \` are removed from spoken text, and the
  no-speech note and plain render.

Checked on 2026-11-02 with ffmpeg 9.0.2 (gyan.dev full build), Windows 11, by
the assistant in a separate process. `analyze` of `test_input.mp4` again was a
cache hit (0.4 s). It gave candidates up to 1 s away from the v0.3 analysis,
as expected after the re-split.

- Whole transcript: 96 segments, all with words, in 271 chunks of 13
  characters at the median and 28 at most. The longest gap inside a chunk was
  0.9 s. Rank 1 had 126 events.
- `render --rank 1 --captions` took 3.0 s and `--vertical --captions` took
  4.4 s, both exit 0, with no `clipper-captions-*` folder left.
- Mid-word frames of the vertical rank 1 at its first word, its middle and its
  last word showed the expected chunk with the expected word in yellow. A
  frame in the longest pause between chunks (9.78 to 11.97 s) showed no
  caption, while the source's own burned-in subtitle showed a line there.
  Whether that pause is silent was not checked by ear.
- The v0.3 analysis printed the note and drew one whole segment, which
  wrapped to two lines.
- Arial, Arial Black and Impact all rendered by name on this build.

## Amended by caption editing (V7 item 9f, ADR 0031)

- The maintainer edits a clip's word captions in the review page's
  Captions tab: a word's text fixed (up to 40 characters, more than one
  word allowed, its time kept), a word hidden (empty text), key words
  shown in the highlight colour throughout their chunk, and that colour
  per clip, one of yellow (the original), green, cyan, pink and orange.
- `CaptionEdits` (`fixes` of `WordFix` by the word's source start,
  `keys` by start, `highlight`) on `ManifestClip.caption_edits` and the
  judged label, None when the captions are as transcribed. Words are
  matched by start time to the millisecond; an edit matching no word
  (after a new transcription) changes nothing.
- `build_ass(..., edits=)` applies them before chunking, so a hidden word
  or a fix ending in `.`, `?` or `!` moves chunk boundaries the same way
  in the burn-in, the preview (`caption_chunks`, `/api/captions?edits=`)
  and the page's chips. Without edits, or with empty ones, the output is
  unchanged (the yellow tag is the same `{\\c&H0000FFFF&}`). Segments
  without word times are not edited.
- Save sends `caption_edits`; it re-renders when they changed and the
  folder burns captions. Checked on v2924392099: a fixed word, a hidden
  word and a pink key word burned into the re-rendered draft. Manual
  step RV21.

## Revisit when

- Word times are visibly off on real clips. Then compare a larger Whisper
  model or the GPU (V4 item 7).
- The re-transcription cost blocks daily use.

## Related

- 0002: Use faster-whisper for transcription
- 0010: Burn in captions from segment-level text
- 0016: Vertical 9:16 clips by a centre crop
