from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from clipper.adapters.transcription.faster_whisper import (
    FasterWhisperTranscriber,
    build_transcript,
)


def test_drops_empty_and_zero_length_segments() -> None:
    transcript = build_transcript(
        [(0, 1, "hi", ()), (1, 1, "zero", ()), (1, 2, "  ", ())]
    )
    assert [s.text for s in transcript.segments] == ["hi"]


def test_clamps_overlapping_start() -> None:
    transcript = build_transcript([(0, 3, "a", ()), (2.5, 5, "b", ())])
    assert transcript.segments[1].start_seconds == 3


def test_strips_text() -> None:
    assert build_transcript([(0, 1, " hello ", ())]).full_text == "hello"


def test_empty_input_gives_empty_transcript() -> None:
    assert build_transcript([]).segments == ()


def test_words_are_stripped_and_kept_in_order() -> None:
    transcript = build_transcript(
        [(0, 2, " Hello there.", [(0.0, 0.8, " Hello"), (0.9, 1.9, " there.")])]
    )
    words = transcript.segments[0].words
    assert [(w.start_seconds, w.end_seconds, w.text) for w in words] == [
        (0.0, 0.8, "Hello"),
        (0.9, 1.9, "there."),
    ]


def test_empty_words_are_dropped() -> None:
    transcript = build_transcript([(0, 2, "a", [(0, 1, " a"), (1, 2, "  ")])])
    assert [w.text for w in transcript.segments[0].words] == ["a"]


def test_words_are_clamped_into_the_segment() -> None:
    # The second segment's start is pushed to 3 by the overlap rule, so its
    # first word, timed from 2.5, must start there too.
    transcript = build_transcript(
        [
            (0, 3, "a", [(0, 3, " a")]),
            (2.5, 5, "b c", [(2.5, 4, " b"), (4, 5.5, " c")]),
        ]
    )
    words = transcript.segments[1].words
    assert [(w.start_seconds, w.end_seconds) for w in words] == [(3, 4), (4, 5)]


def test_overlapping_words_are_pushed_later() -> None:
    transcript = build_transcript([(0, 2, "a b", [(0, 1.2, " a"), (1.0, 1.5, " b")])])
    words = transcript.segments[0].words
    assert [(w.start_seconds, w.end_seconds) for w in words] == [(0, 1.2), (1.2, 1.5)]


def test_word_with_no_time_left_keeps_zero_length() -> None:
    transcript = build_transcript([(0, 1, "a b", [(0, 1, " a"), (0.9, 1.0, " b")])])
    words = transcript.segments[0].words
    assert [(w.start_seconds, w.end_seconds, w.text) for w in words] == [
        (0, 1, "a"),
        (1, 1, "b"),
    ]


def test_token_without_leading_space_joins_the_previous_word() -> None:
    # Raw words as faster-whisper 1.2.1 returned them for test_input.mp4.
    transcript = build_transcript(
        [
            (
                0,
                4,
                " tough battle-hardened SEALs,",
                [
                    (0.0, 0.5, " tough"),
                    (0.5, 1.0, " battle"),
                    (1.0, 1.8, "-hardened"),
                    (1.8, 2.5, " SEALs,"),
                ],
            )
        ]
    )
    words = transcript.segments[0].words
    assert [(w.start_seconds, w.end_seconds, w.text) for w in words] == [
        (0.0, 0.5, "tough"),
        (0.5, 1.8, "battle-hardened"),
        (1.8, 2.5, "SEALs,"),
    ]


def test_first_token_without_leading_space_stays_a_word() -> None:
    transcript = build_transcript([(0, 1, "'twas", [(0, 1, "'twas")])])
    assert [w.text for w in transcript.segments[0].words] == ["'twas"]


def test_name_marks_word_timestamps() -> None:
    assert FasterWhisperTranscriber("small").name == "faster-whisper-small+words"


class _FakeModel:
    """Stands in for WhisperModel; records the transcribe call."""

    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def transcribe(self, path: str, **kwargs: Any) -> tuple[list[Any], None]:
        self.kwargs = kwargs
        word = SimpleNamespace(start=0.1, end=0.6, word=" hi", probability=0.9)
        segment = SimpleNamespace(start=0.0, end=0.6, text=" hi", words=[word])
        return [segment], None


def test_transcribe_asks_for_word_timestamps_and_keeps_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = tmp_path / "a.wav"
    media.write_bytes(b"x")
    model = _FakeModel()
    transcriber = FasterWhisperTranscriber()
    monkeypatch.setattr(transcriber, "_load_model", lambda: model)

    transcript = transcriber.transcribe(media)

    assert model.kwargs == {"vad_filter": True, "word_timestamps": True}
    word = transcript.segments[0].words[0]
    assert (word.start_seconds, word.end_seconds, word.text) == (0.1, 0.6, "hi")
