from collections.abc import Iterable

from clipper.domain.models import ClipCandidate, Segment, Transcript

# Shared by every detector that scores the same windows (ADR 0014).
WINDOW_SECONDS = 45.0
STEP_SECONDS = 15.0
# Candidate bounds (ADR 0023): windows under MIN_CANDIDATE_SECONDS are not
# scored (item 6 amendment); an end moves to the next pause of at least
# PAUSE_SECONDS, set from the V5 baseline's dragged ends (item 5 amendment).
MIN_CANDIDATE_SECONDS = 15.0
MAX_CANDIDATE_SECONDS = 90.0
PAUSE_SECONDS = 1.5


def sliding_windows(
    transcript: Transcript,
    window_seconds: float = WINDOW_SECONDS,
    step_seconds: float = STEP_SECONDS,
    min_seconds: float = MIN_CANDIDATE_SECONDS,
) -> list[tuple[Segment, ...]]:
    """Whole-segment windows, stepped from the first segment's start.

    A segment longer than the window is in no window, and a window spanning
    less than `min_seconds` is dropped. Identical segment sets from adjacent
    steps are kept once.
    """
    segments = transcript.segments
    if not segments:
        return []
    windows: list[tuple[Segment, ...]] = []
    start = segments[0].start_seconds
    last_end = segments[-1].end_seconds
    while start < last_end:
        inside = tuple(
            s
            for s in segments
            if s.start_seconds >= start and s.end_seconds <= start + window_seconds
        )
        if inside and inside[-1].end_seconds - inside[0].start_seconds >= min_seconds:
            windows.append(inside)
        start += step_seconds
    # Identical segment sets from adjacent steps are the same window.
    unique = {(w[0].start_seconds, w[-1].end_seconds): w for w in windows}
    return list(unique.values())


def select_top(candidates: Iterable[ClipCandidate], top_n: int) -> list[ClipCandidate]:
    """Highest score first (ties by earlier start), skipping overlaps with one chosen.

    Touching candidates do not overlap.
    """
    ranked = sorted(candidates, key=lambda c: (-c.score, c.start_seconds))
    chosen: list[ClipCandidate] = []
    for candidate in ranked:
        if all(not _overlaps(candidate, other) for other in chosen):
            chosen.append(candidate)
        if len(chosen) == top_n:
            break
    return chosen


def _overlaps(a: ClipCandidate, b: ClipCandidate) -> bool:
    return a.start_seconds < b.end_seconds and b.start_seconds < a.end_seconds


def extend_ends(
    chosen: list[ClipCandidate],
    transcript: Transcript,
    pause_seconds: float = PAUSE_SECONDS,
    max_seconds: float = MAX_CANDIDATE_SECONDS,
) -> list[ClipCandidate]:
    """Move each chosen candidate's end to the next pause in speech, in order.

    The end moves word by word, or segment by segment where a segment has no
    words, until the gap to the next is at least `pause_seconds`. It never
    goes past `max_seconds` from the start nor into a later chosen candidate.
    Starts do not change.
    """
    units = _speech_units(transcript)
    starts = [c.start_seconds for c in chosen]
    extended: list[ClipCandidate] = []
    for candidate in chosen:
        limit = min(
            [s for s in starts if s >= candidate.end_seconds],
            default=candidate.start_seconds + max_seconds,
        )
        limit = min(limit, candidate.start_seconds + max_seconds)
        end = candidate.end_seconds
        for start, unit_end in units:
            if unit_end <= end:
                continue
            if start - end >= pause_seconds or unit_end > limit:
                break
            end = unit_end
        extended.append(candidate.model_copy(update={"end_seconds": end}))
    return extended


def _speech_units(transcript: Transcript) -> list[tuple[float, float]]:
    """Word spans, or the segment's span for a segment without words."""
    units: list[tuple[float, float]] = []
    for segment in transcript.segments:
        if segment.words:
            units.extend((w.start_seconds, w.end_seconds) for w in segment.words)
        else:
            units.append((segment.start_seconds, segment.end_seconds))
    return units
