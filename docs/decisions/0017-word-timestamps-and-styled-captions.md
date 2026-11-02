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

## Revisit when

- Word times are visibly off on real clips. Then compare a larger Whisper
  model or the GPU (V4 item 7).
- The re-transcription cost blocks daily use.

## Related

- 0002: Use faster-whisper for transcription
- 0010: Burn in captions from segment-level text
- 0016: Vertical 9:16 clips by a centre crop
