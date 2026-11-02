import json
import logging

import pytest

from clipper.adapters.detection.llm import SYSTEM_PROMPT, LlmClipDetector, WindowScore
from clipper.adapters.detection.windows import sliding_windows
from clipper.core.interfaces import ClipDetector
from clipper.domain.errors import DetectionError
from clipper.domain.models import Segment, Transcript

# All replies here are constructed, not produced by a real model.


def _transcript(n: int = 12) -> Transcript:
    # n segments x 10 s, each naming its index so a reply can depend on it.
    return Transcript(
        segments=tuple(
            Segment(start_seconds=i * 10.0, end_seconds=i * 10.0 + 10, text=f"s{i} ")
            for i in range(n)
        )
    )


def _reply(score: object, reason: object = "a reason") -> str:
    return json.dumps({"score": score, "reason": reason})


class FakeGenerator:
    def __init__(self, reply: str | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self._reply = reply

    def generate(self, system: str, prompt: str, schema: dict[str, object]) -> str:
        self.calls.append((system, prompt, schema))
        if self._reply is not None:
            return self._reply
        # Score by the first segment's index: later windows score higher.
        first = int(prompt.split()[0].removeprefix("s"))
        return _reply(first / 100, f"window from s{first}")


def test_satisfies_protocol() -> None:
    assert isinstance(LlmClipDetector(FakeGenerator()), ClipDetector)


def test_empty_transcript_makes_no_calls() -> None:
    generator = FakeGenerator()
    assert LlmClipDetector(generator).detect(Transcript(segments=())) == []
    assert generator.calls == []


def test_scores_every_window_once_in_order() -> None:
    transcript = _transcript()
    generator = FakeGenerator()

    LlmClipDetector(generator).detect(transcript)

    windows = sliding_windows(transcript)
    assert [prompt for _, prompt, _ in generator.calls] == [
        " ".join(s.text.strip() for s in w) for w in windows
    ]


def test_sends_the_system_prompt_and_the_score_schema() -> None:
    generator = FakeGenerator()
    LlmClipDetector(generator).detect(_transcript())
    system, _, schema = generator.calls[0]
    assert system == SYSTEM_PROMPT
    assert schema == WindowScore.model_json_schema()


def test_ranks_by_model_score_without_overlap() -> None:
    result = LlmClipDetector(FakeGenerator(), top_n=2).detect(_transcript())

    # The latest start scores highest: the tail window s11 (110-120). The
    # windows from s8 and s9 reach 120 and overlap it; s6 (60-100) does not.
    assert [(c.start_seconds, c.end_seconds) for c in result] == [
        (110.0, 120.0),
        (60.0, 100.0),
    ]
    assert [c.score for c in result] == [0.11, 0.06]
    assert result[0].reasons == ("window from s11",)


def test_reason_is_stripped() -> None:
    result = LlmClipDetector(FakeGenerator(_reply(0.5, "  padded  "))).detect(
        _transcript(4)
    )
    assert result[0].reasons == ("padded",)


@pytest.mark.parametrize(
    ("reply", "detail"),
    [
        ("not json", "<root>: Invalid JSON"),
        (_reply(1.5), "score: Input should be less than or equal to 1"),
        (_reply(-0.1), "score: Input should be greater than or equal to 0"),
        (_reply("high"), "score: Input should be a valid number"),
        (_reply(0.5, "  "), "reason: String should have at least 1 character"),
        (json.dumps({"score": 0.5}), "reason: Field required"),
    ],
)
def test_invalid_reply_raises_and_names_the_window(reply: str, detail: str) -> None:
    with pytest.raises(DetectionError) as info:
        LlmClipDetector(FakeGenerator(reply)).detect(_transcript(4))
    message = str(info.value)
    assert message.startswith("Model reply for window 0:00:00-0:00:40")
    assert detail in message
    assert repr(reply[:200]) in message


def test_generator_error_propagates() -> None:
    class Failing:
        def generate(self, system: str, prompt: str, schema: dict[str, object]) -> str:
            raise DetectionError("Cannot reach Ollama")

    with pytest.raises(DetectionError, match="Cannot reach Ollama"):
        LlmClipDetector(Failing()).detect(_transcript())


def test_logs_progress(caplog: pytest.LogCaptureFixture) -> None:
    transcript = _transcript()
    total = len(sliding_windows(transcript))
    with caplog.at_level(logging.INFO, logger="clipper.adapters.detection.llm"):
        LlmClipDetector(FakeGenerator()).detect(transcript)
    assert f"Scored {total} of {total} windows" in caplog.text
