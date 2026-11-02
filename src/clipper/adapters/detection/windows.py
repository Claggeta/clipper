from collections.abc import Iterable

from clipper.domain.models import ClipCandidate, Segment, Transcript

# Shared by every detector that scores the same windows (ADR 0014).
WINDOW_SECONDS = 45.0
STEP_SECONDS = 15.0


def sliding_windows(
    transcript: Transcript,
    window_seconds: float = WINDOW_SECONDS,
    step_seconds: float = STEP_SECONDS,
) -> list[tuple[Segment, ...]]:
    """Whole-segment windows, stepped from the first segment's start.

    A segment longer than the window is in no window. Identical segment sets
    from adjacent steps are kept once.
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
        if inside:
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
