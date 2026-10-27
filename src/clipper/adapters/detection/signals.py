import re
from collections.abc import Callable
from dataclasses import dataclass

from clipper.domain.models import Segment

# A signal looks at the segments in a window and returns (strength 0-1, reason).
SignalResult = tuple[float, str] | None
Signal = Callable[[tuple[Segment, ...]], SignalResult]

_EMPHASIS_WORDS = frozenset(
    {
        "never", "always", "secret", "mistake", "truth", "crazy", "insane",
        "incredible", "biggest", "worst", "best", "shocking", "honestly",
        "actually", "love", "hate", "fear", "money", "free", "stop",
    }
)  # fmt: skip


def _text(segments: tuple[Segment, ...]) -> str:
    return " ".join(s.text for s in segments)


def question_signal(segments: tuple[Segment, ...]) -> SignalResult:
    count = _text(segments).count("?")
    if count == 0:
        return None
    return min(count / 3, 1.0), f"contains {count} question(s)"


def exclamation_signal(segments: tuple[Segment, ...]) -> SignalResult:
    count = _text(segments).count("!")
    if count == 0:
        return None
    return min(count / 3, 1.0), f"contains {count} exclamation(s)"


def emphasis_signal(segments: tuple[Segment, ...]) -> SignalResult:
    words = re.findall(r"[a-z']+", _text(segments).lower())
    hits = sorted({w for w in words if w in _EMPHASIS_WORDS})
    if not hits:
        return None
    return min(len(hits) / 3, 1.0), "strong words: " + ", ".join(hits)


def pace_signal(segments: tuple[Segment, ...]) -> SignalResult:
    duration = segments[-1].end_seconds - segments[0].start_seconds
    if duration <= 0:
        return None
    words_per_second = len(_text(segments).split()) / duration
    # About 2 words/second is calm speech; 3+ is fast and energetic.
    strength = min(max((words_per_second - 2.0) / 1.5, 0.0), 1.0)
    if strength == 0.0:
        return None
    return strength, f"fast pace ({words_per_second:.1f} words/sec)"


@dataclass(frozen=True)
class WeightedSignal:
    signal: Signal
    weight: float


DEFAULT_SIGNALS: tuple[WeightedSignal, ...] = (
    WeightedSignal(question_signal, 0.30),
    WeightedSignal(exclamation_signal, 0.15),
    WeightedSignal(emphasis_signal, 0.35),
    WeightedSignal(pace_signal, 0.20),
)
