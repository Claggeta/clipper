# 0004: Report ffmpeg's actual error instead of the stderr tail

- Status: Accepted (the "adapter stays agnostic" clause is amended by 0005)
- Date: 2026-10-30

## Context

When `clipper analyze` ran on a real video for the first time, audio extraction
failed and the error message the CLI printed did not explain why. The message
came from `FfmpegAudioExtractor.extract`, which read:

    f"ffmpeg failed (exit {result.returncode}): {result.stderr[-500:]}"

Two things were wrong with that. First, the command ran without `-hide_banner`
or `-loglevel`, so ffmpeg's stderr contained the version banner, the build
configuration, the input's container metadata, and the stream mapping, with the
actual cause somewhere in the middle as a short line. Second — the part that
made the cause invisible — a 500-character window anchored at the end of that
output lands on the trailing stream metadata, which is the least informative
part of the noise. The message was accurate and useless: it reported the tail of
ffmpeg's output rather than the reason it exited.

The failure was reproducible and the cause was not discoverable from the CLI,
which meant the user had to re-run ffmpeg by hand to see what went wrong. That
is the specific cost this decision removes.

## Decision

Three changes, made together because they only work as a set:

1. Pass `-hide_banner -loglevel error` to ffmpeg, so stderr contains only
   errors. The cause is now most of what arrives, and truncation stops being
   load-bearing.
2. `AudioExtractionError` carries `ffmpeg failed (exit N): <stderr>`, where
   `stderr` is stripped and reduced to its **last 10 non-empty lines** — capped
   by line count, never by character count, so a line is never split
   mid-sentence. With `-loglevel error` this is usually the whole text; the cap
   only guards against a pathological multi-error run.
3. The **complete untruncated stderr** goes to `logger.info`. It appears with
   `-v` and not otherwise. INFO rather than WARNING because the exception is
   already printed by the CLI, and logging at WARNING would print the same
   failure twice on the default path.

The adapter stays agnostic about *why* ffmpeg failed. It does not parse stderr
to classify failure modes; it carries whatever ffmpeg said.

A fourth change rides along, because it is the same user-visible outcome: the
CLI now prints `escape(str(exc))` instead of `str(exc)`. ffmpeg's error lines
commonly begin with a bracketed prefix such as `[aist#0:0/pcm_s16le @ 0x...]`,
and rich parses `[...]` as markup — swallowing those prefixes, or raising
`MarkupError` on `[/...]`. Without the escape, fixing the message would have
surfaced ffmpeg's stderr only to have the console mangle it.

## Alternatives considered

- **Surface the full stderr verbatim in the CLI message.** Rejected. Nothing is
  lost, but a badly broken file emits dozens of lines and the error becomes a
  wall of red text — trading an unreadable message for an oversized one.
- **Keep a character-count window, but bias it to the head instead of the
  tail.** Rejected as the primary fix. It is still an arbitrary boundary, and it
  still splits lines. It would have helped for this failure and hidden the next
  one.
- **Truncate on a different character count.** Rejected. The window size was
  never the problem; the location of the window was.
- **Reduce noise at the source but keep the old message.** Rejected as
  insufficient on its own — but the inverse is equally true: truncating by lines
  without `-loglevel error` would still spend the 10-line budget on banner and
  metadata. The two changes are only useful together.
- **Log at WARNING instead of INFO.** Rejected. It duplicates the message the
  CLI already prints in the common case, where the user is waiting on stderr
  rather than reading logs.
- **Classify failure modes inside the adapter** (e.g. detect "no audio stream"
  here and raise a distinct error). Rejected *for this ADR*, and deferred.
  Parsing another program's stderr is brittle across versions and locales, and
  the no-audio case has a different user message and deserves its own design
  discussion. Deferred to the no-audio work, where the tradeoff between string
  matching and an `ffprobe` pre-check will be decided. See "Revisit when".
  **Decided in 0005: string match in the extractor.**

## Consequences

- (+) The cause is what the user reads. Verified against the installed ffmpeg on
  a text file named `bad.mp4`: the message now leads with `moov atom not found`
  and `Invalid data found when processing input`, where the old tail slice
  showed stream metadata.
- (+) Nothing is lost. The full stderr is in the log at INFO, one `-v` away.
- (+) The message never splits a line mid-sentence, so it stays readable at any
  length.
- (+) `-hide_banner -loglevel error` also makes the log itself usable, since it
  no longer carries a version banner into the INFO output.
- (+) Bracketed prefixes from ffmpeg render literally in the console rather than
  being eaten by rich.
- (−) `-loglevel error` is now **load-bearing for the message being readable**,
  and that dependency is not obvious from the flag. A future reader may see it
  as arbitrary and remove it, at which point the message silently degrades back
  toward noise. This ADR is the answer to that, and the flag is asserted in a
  test.
- (−) The tests mock `subprocess.run`, so they assert the adapter's handling of
  stderr, not ffmpeg's real behaviour. Nothing in CI confirms that
  `-loglevel error` leaves the cause line on stderr *for a given ffmpeg build*.
  That verification is manual, and belongs to the manual test checklist.
- (−) The 10-line cap is a judgement call, not a measured one. A failure that
  emits more than ten pre-terminal error lines loses the earlier ones from the
  message; the log still has them.
- (−) ffmpeg's exit code is passed through unmodified, and on Windows it can be
  a negative `AVERROR` rendered as a huge unsigned integer (`3199971767` for
  `AVERROR_INVALIDDATA`). Accurate but unreadable. Deliberately out of scope
  here; parked.

## Revisit when

- The no-audio work lands and decides between matching ffmpeg's stderr and
  probing with `ffprobe`. If a probe adapter is introduced, this decision's "the
  adapter stays agnostic" clause applies only to the extractor, and the probe
  reports structurally instead.
- Someone proposes removing `-hide_banner -loglevel error`. That is only safe if
  the message stops depending on ffmpeg's stderr being quiet.
- The 10-line cap proves wrong in practice — an error whose useful content is in
  its early lines.
- A future change introduces a second place that formats ffmpeg output, at which
  point the line-capping logic should be a shared function rather than a second
  copy.

## Related

- 0002: Use faster-whisper for transcription
- 0003: Pin transcription dependencies
- PR #18: `fix(media): report ffmpeg's actual error instead of stderr tail`
