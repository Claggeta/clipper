from clipper.adapters.detection.signals import (
    emphasis_signal,
    exclamation_signal,
    pace_signal,
    question_signal,
)
from clipper.domain.models import Segment


def _segs(text: str, end: float = 10.0) -> tuple[Segment, ...]:
    return (Segment(start_seconds=0.0, end_seconds=end, text=text),)


def test_question_signal() -> None:
    assert question_signal(_segs("Why? How?")) == (2 / 3, "contains 2 question(s)")
    assert question_signal(_segs("Plain statement.")) is None


def test_exclamation_signal() -> None:
    assert exclamation_signal(_segs("Wow!")) is not None
    assert exclamation_signal(_segs("Calm.")) is None


def test_emphasis_signal_lists_words() -> None:
    result = emphasis_signal(_segs("The secret is never quit."))
    assert result is not None
    assert "never" in result[1] and "secret" in result[1]


def test_pace_signal_fast_vs_slow() -> None:
    fast = " ".join(["word"] * 40)  # 4 words/sec over 10s
    slow = " ".join(["word"] * 10)  # 1 word/sec
    assert pace_signal(_segs(fast)) is not None
    assert pace_signal(_segs(slow)) is None
