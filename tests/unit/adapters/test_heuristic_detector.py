from clipper.adapters.detection.heuristic import HeuristicClipDetector
from clipper.adapters.transcription.fake import FakeTranscriber
from clipper.core.interfaces import ClipDetector
from clipper.domain.models import Segment, Transcript


def _long_transcript() -> Transcript:
    segments = []
    for i in range(20):  # 20 segments x 10s = 200s
        text = "This is the secret! Why? Honestly." if i % 5 == 0 else "Calm words."
        segments.append(
            Segment(start_seconds=i * 10.0, end_seconds=i * 10.0 + 10, text=text)
        )
    return Transcript(segments=tuple(segments))


def test_satisfies_protocol() -> None:
    assert isinstance(HeuristicClipDetector(), ClipDetector)


def test_empty_transcript_gives_no_candidates() -> None:
    assert HeuristicClipDetector().detect(Transcript(segments=())) == []


def test_returns_at_most_top_n() -> None:
    assert len(HeuristicClipDetector(top_n=2).detect(_long_transcript())) <= 2


def test_candidates_do_not_overlap_and_are_ranked() -> None:
    result = HeuristicClipDetector().detect(_long_transcript())
    assert result
    scores = [c.score for c in result]
    assert scores == sorted(scores, reverse=True)
    for i, a in enumerate(result):
        for b in result[i + 1 :]:
            assert a.end_seconds <= b.start_seconds or b.end_seconds <= a.start_seconds


def test_detection_is_deterministic() -> None:
    detector = HeuristicClipDetector()
    transcript = _long_transcript()
    assert detector.detect(transcript) == detector.detect(transcript)


def test_works_with_fake_transcriber_output() -> None:
    from pathlib import Path

    transcript = FakeTranscriber().transcribe(Path("x.mp4"))
    assert HeuristicClipDetector().detect(transcript)


def test_end_extends_to_the_next_pause() -> None:
    # 5 s segments with no gaps, then a 2 s pause after 60 s.
    segments = [
        Segment(start_seconds=i * 5.0, end_seconds=i * 5.0 + 5, text="Why? Wow!")
        for i in range(12)
    ]
    segments.append(Segment(start_seconds=62.0, end_seconds=65.0, text="Calm."))

    result = HeuristicClipDetector(top_n=1).detect(Transcript(segments=tuple(segments)))

    # The best window starts at 0 s; its end moves from 45 s to the pause at 60 s.
    assert [(c.start_seconds, c.end_seconds) for c in result] == [(0.0, 60.0)]
