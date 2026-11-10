from collections.abc import Sequence
from dataclasses import dataclass

from clipper.domain.models import (
    ClipCandidate,
    Evaluation,
    Label,
    LabeledVideo,
    Span,
)

# A first guess, not tuned on real labels (ADR 0012). A constant rather than a
# setting, so it is not fitted to one video before a long one is labeled.
HIT_FRACTION = 0.5

# A keeper ends early or starts late when its label reaches more than this past
# the candidate (ADR 0012, V5 amendment). A drag on a slider is good to about
# half a second.
CUT_SHORT_SECONDS = 1.0


def _to_ms(seconds: float) -> int:
    return round(seconds * 1000)


def is_hit(candidate: ClipCandidate, label: Span) -> bool:
    """True if the overlap is at least HIT_FRACTION of the shorter range.

    Times are converted to whole milliseconds first so float noise cannot move
    a result at the boundary. Touching ranges do not overlap, so never hit.
    """
    c_start, c_end = _to_ms(candidate.start_seconds), _to_ms(candidate.end_seconds)
    l_start, l_end = _to_ms(label.start_seconds), _to_ms(label.end_seconds)
    overlap = min(c_end, l_end) - max(c_start, l_start)
    shorter = min(c_end - c_start, l_end - l_start)
    return overlap > 0 and overlap >= HIT_FRACTION * shorter


def is_draft_of(candidate: ClipCandidate, label: Label) -> bool:
    """True if the candidate's span is the `fix` label's draft, to the ms.

    The maintainer judged that draft, however far the fix moved it (ADR 0012,
    draft span amendment).
    """
    return label.draft is not None and (
        _to_ms(candidate.start_seconds),
        _to_ms(candidate.end_seconds),
    ) == (_to_ms(label.draft.start_seconds), _to_ms(label.draft.end_seconds))


def evaluate(labeled: LabeledVideo, candidates: Sequence[ClipCandidate]) -> Evaluation:
    """Compare a detector's candidates with a video's labels. See ADR 0012.

    A candidate hits a label it overlaps enough, or a `fix` label whose draft
    it is. Candidates are matched in rank order: each claims the earliest
    label it hits that no higher-ranked candidate has claimed. With judged
    labels, a candidate that hits no label but hits a rejected span is
    `on_rejected`.
    """
    hits = tuple(
        (label_index, candidate_index)
        for label_index, label in enumerate(labeled.labels)
        for candidate_index, candidate in enumerate(candidates)
        if is_hit(candidate, label) or is_draft_of(candidate, label)
    )
    claimed: set[int] = set()
    matches: list[tuple[int, int]] = []
    for candidate_index in range(len(candidates)):
        free = [i for i, c in hits if c == candidate_index and i not in claimed]
        if free:
            claimed.add(free[0])
            matches.append((free[0], candidate_index))
    hit = {candidate_index for _, candidate_index in hits}
    on_rejected = tuple(
        candidate_index
        for candidate_index, candidate in enumerate(candidates)
        if candidate_index not in hit
        and any(is_hit(candidate, span) for span in labeled.rejected)
    )
    return Evaluation(
        source=labeled.source,
        labels=labeled.labels,
        candidates=tuple(candidates),
        hits=hits,
        matches=tuple(matches),
        judged=labeled.judged,
        on_rejected=on_rejected,
    )


@dataclass(frozen=True)
class BoundaryGap:
    """How far a matched candidate's ends are from its label's, in seconds.

    Positive `end` means the label ends after the candidate; negative `start`
    means the label starts before it.
    """

    candidate_index: int
    start: float
    end: float

    @property
    def ends_early(self) -> bool:
        return _to_ms(self.end) > _to_ms(CUT_SHORT_SECONDS)

    @property
    def starts_late(self) -> bool:
        return _to_ms(-self.start) > _to_ms(CUT_SHORT_SECONDS)


def boundary_gaps(evaluation: Evaluation) -> list[BoundaryGap]:
    """Label minus candidate, start and end, for each match on a judged label.

    In rank order. For the run that produced a draft, the gap is the drag the
    maintainer made in the review page; after a boundary change it shows
    whether the new candidates end where the maintainer would end them.
    """
    gaps = []
    for label_index, candidate_index in sorted(
        evaluation.matches, key=lambda match: match[1]
    ):
        label = evaluation.labels[label_index]
        if label.verdict is None:
            continue
        candidate = evaluation.candidates[candidate_index]
        gaps.append(
            BoundaryGap(
                candidate_index=candidate_index,
                start=(_to_ms(label.start_seconds) - _to_ms(candidate.start_seconds))
                / 1000,
                end=(_to_ms(label.end_seconds) - _to_ms(candidate.end_seconds)) / 1000,
            )
        )
    return gaps
