from clipper.adapters.transcription.faster_whisper import build_transcript


def test_drops_empty_and_zero_length_segments() -> None:
    transcript = build_transcript([(0, 1, "hi"), (1, 1, "zero"), (1, 2, "  ")])
    assert [s.text for s in transcript.segments] == ["hi"]


def test_clamps_overlapping_start() -> None:
    transcript = build_transcript([(0, 3, "a"), (2.5, 5, "b")])
    assert transcript.segments[1].start_seconds == 3


def test_strips_text() -> None:
    assert build_transcript([(0, 1, " hello ")]).full_text == "hello"


def test_empty_input_gives_empty_transcript() -> None:
    assert build_transcript([]).segments == ()
