from clipper.domain.models import ClipCandidate, Transcript


def _to_ms(seconds: float) -> int:
    return round(seconds * 1000)


def _timestamp(ms: int) -> str:
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def build_srt(transcript: Transcript, candidate: ClipCandidate) -> str:
    """Build SRT text for the part of `transcript` inside the candidate's span.

    See docs/decisions/0010. Segments that overlap the span by a positive
    amount are kept, clamped to it, and shifted so the clip starts at zero.
    A straddling segment keeps its full text, since there are no word times
    to cut it by. Times are converted to whole milliseconds before clamping
    so float noise cannot move a cue. Whitespace in the text, newlines
    included, collapses to single spaces, because a blank line would end the
    cue. Cues that are empty or have no length after rounding are dropped.

    Returns "" when no cue remains. The caller decides what that means.
    """
    clip_start = _to_ms(candidate.start_seconds)
    clip_end = _to_ms(candidate.end_seconds)
    cues: list[str] = []
    for segment in transcript.segments:
        start = max(_to_ms(segment.start_seconds), clip_start)
        end = min(_to_ms(segment.end_seconds), clip_end)
        text = " ".join(segment.text.split())
        if end <= start or not text:
            continue
        cues.append(
            f"{len(cues) + 1}\n"
            f"{_timestamp(start - clip_start)} --> {_timestamp(end - clip_start)}\n"
            f"{text}\n"
        )
    return "\n".join(cues)
