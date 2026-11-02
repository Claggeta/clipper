from collections.abc import Sequence

from clipper.domain.models import ClipCandidate, Evaluation, Label, LabeledVideo

# A first guess, not tuned on real labels (ADR 0012). A constant rather than a
# setting, so it is not fitted to one video before a long one is labeled.
HIT_FRACTION = 0.5


def _to_ms(seconds: float) -> int:
    return round(seconds * 1000)


def is_hit(candidate: ClipCandidate, label: Label) -> bool:
    """True if the overlap is at least HIT_FRACTION of the shorter range.

    Times are converted to whole milliseconds first so float noise cannot move
    a result at the boundary. Touching ranges do not overlap, so never hit.
    """
    c_start, c_end = _to_ms(candidate.start_seconds), _to_ms(candidate.end_seconds)
    l_start, l_end = _to_ms(label.start_seconds), _to_ms(label.end_seconds)
    overlap = min(c_end, l_end) - max(c_start, l_start)
    shorter = min(c_end - c_start, l_end - l_start)
    return overlap > 0 and overlap >= HIT_FRACTION * shorter


def evaluate(labeled: LabeledVideo, candidates: Sequence[ClipCandidate]) -> Evaluation:
    """Compare a detector's candidates with a video's labels. See ADR 0012.

    Candidates are matched in rank order: each claims the earliest label it
    hits that no higher-ranked candidate has claimed.
    """
    hits = tuple(
        (label_index, candidate_index)
        for label_index, label in enumerate(labeled.labels)
        for candidate_index, candidate in enumerate(candidates)
        if is_hit(candidate, label)
    )
    claimed: set[int] = set()
    matches: list[tuple[int, int]] = []
    for candidate_index in range(len(candidates)):
        free = [i for i, c in hits if c == candidate_index and i not in claimed]
        if free:
            claimed.add(free[0])
            matches.append((free[0], candidate_index))
    return Evaluation(
        source=labeled.source,
        labels=labeled.labels,
        candidates=tuple(candidates),
        hits=hits,
        matches=tuple(matches),
    )
