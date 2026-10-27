from clipper.adapters.detection.signals import DEFAULT_SIGNALS, WeightedSignal
from clipper.domain.models import ClipCandidate, Segment, Transcript


class HeuristicClipDetector:
    """Scores sliding windows of a transcript using simple text signals."""

    def __init__(
        self,
        top_n: int = 5,
        window_seconds: float = 45.0,
        step_seconds: float = 15.0,
        min_score: float = 0.0,
        signals: tuple[WeightedSignal, ...] = DEFAULT_SIGNALS,
    ) -> None:
        self._top_n = top_n
        self._window = window_seconds
        self._step = step_seconds
        self._min_score = min_score
        self._signals = signals

    def detect(self, transcript: Transcript) -> list[ClipCandidate]:
        windows = self._windows(transcript)
        scored = [c for w in windows if (c := self._score(w)) is not None]
        scored = [c for c in scored if c.score > self._min_score]
        scored.sort(key=lambda c: (-c.score, c.start_seconds))

        chosen: list[ClipCandidate] = []
        for candidate in scored:
            if all(not _overlaps(candidate, other) for other in chosen):
                chosen.append(candidate)
            if len(chosen) == self._top_n:
                break
        return chosen

    def _windows(self, transcript: Transcript) -> list[tuple[Segment, ...]]:
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
                if s.start_seconds >= start and s.end_seconds <= start + self._window
            )
            if inside:
                windows.append(inside)
            start += self._step
        # Identical segment sets from adjacent steps are the same window.
        unique = {(w[0].start_seconds, w[-1].end_seconds): w for w in windows}
        return list(unique.values())

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


def _overlaps(a: ClipCandidate, b: ClipCandidate) -> bool:
    return a.start_seconds < b.end_seconds and b.start_seconds < a.end_seconds
