from clipper.adapters.detection.signals import DEFAULT_SIGNALS, WeightedSignal
from clipper.adapters.detection.windows import (
    STEP_SECONDS,
    WINDOW_SECONDS,
    select_top,
    sliding_windows,
)
from clipper.domain.models import ClipCandidate, Segment, Transcript


class HeuristicClipDetector:
    """Scores sliding windows of a transcript using simple text signals."""

    def __init__(
        self,
        top_n: int = 5,
        window_seconds: float = WINDOW_SECONDS,
        step_seconds: float = STEP_SECONDS,
        min_score: float = 0.0,
        signals: tuple[WeightedSignal, ...] = DEFAULT_SIGNALS,
    ) -> None:
        self._top_n = top_n
        self._window = window_seconds
        self._step = step_seconds
        self._min_score = min_score
        self._signals = signals

    def detect(self, transcript: Transcript) -> list[ClipCandidate]:
        windows = sliding_windows(transcript, self._window, self._step)
        scored = [c for w in windows if (c := self._score(w)) is not None]
        scored = [c for c in scored if c.score > self._min_score]
        return select_top(scored, self._top_n)

    def _score(self, window: tuple[Segment, ...]) -> ClipCandidate | None:
        total = 0.0
        reasons: list[str] = []
        for weighted in self._signals:
            result = weighted.signal(window)
            if result is None:
                continue
            strength, reason = result
            total += weighted.weight * strength
            reasons.append(reason)
        if not reasons:
            return None
        weight_sum = sum(w.weight for w in self._signals)
        return ClipCandidate(
            start_seconds=window[0].start_seconds,
            end_seconds=window[-1].end_seconds,
            score=min(total / weight_sum, 1.0),
            reasons=tuple(reasons),
        )
