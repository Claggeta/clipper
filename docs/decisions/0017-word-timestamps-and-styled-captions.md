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

## Revisit when

- Word times are visibly off on real clips. Then compare a larger Whisper
  model or the GPU (V4 item 7).
- The re-transcription cost blocks daily use.

## Related

- 0002: Use faster-whisper for transcription
- 0010: Burn in captions from segment-level text
- 0016: Vertical 9:16 clips by a centre crop
