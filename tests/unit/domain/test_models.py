from pathlib import Path

import pytest
from pydantic import ValidationError

from clipper.domain.models import (
    ClipCandidate,
    MediaInfo,
    RenderedClip,
    Segment,
    Transcript,
)


def test_segment_duration() -> None:
    segment = Segment(start_seconds=1.0, end_seconds=3.5, text="hello")
    assert segment.duration_seconds == 2.5


def test_segment_rejects_end_before_start() -> None:
    with pytest.raises(ValidationError):
        Segment(start_seconds=5.0, end_seconds=2.0, text="bad")


def test_segment_rejects_negative_start() -> None:
    with pytest.raises(ValidationError):
        Segment(start_seconds=-1.0, end_seconds=2.0, text="bad")


def test_segment_is_immutable() -> None:
    segment = Segment(start_seconds=0.0, end_seconds=1.0, text="hello")
    with pytest.raises(ValidationError):
        segment.text = "changed"  # type: ignore[misc]


def _seg(start: float, end: float, text: str) -> Segment:
    return Segment(start_seconds=start, end_seconds=end, text=text)


def test_transcript_full_text_joins_segments() -> None:
    transcript = Transcript(segments=[_seg(0, 1, "hello"), _seg(1, 2, "world")])
    assert transcript.full_text == "hello world"


def test_transcript_allows_empty() -> None:
    assert Transcript(segments=[]).full_text == ""


def test_transcript_rejects_out_of_order_segments() -> None:
    with pytest.raises(ValidationError):
        Transcript(segments=[_seg(5, 6, "b"), _seg(0, 1, "a")])


def test_transcript_rejects_overlapping_segments() -> None:
    with pytest.raises(ValidationError):
        Transcript(segments=[_seg(0, 3, "a"), _seg(2, 4, "b")])


def test_transcript_duration_is_last_segment_end() -> None:
    transcript = Transcript(segments=[_seg(0, 1, "a"), _seg(2, 7.5, "b")])
    assert transcript.duration_seconds == 7.5


def _candidate(**overrides: object) -> ClipCandidate:
    fields: dict[str, object] = {
        "start_seconds": 10.0,
        "end_seconds": 40.0,
        "score": 0.8,
        "reasons": ["asks a question"],
    }
    fields.update(overrides)
    return ClipCandidate(**fields)


def test_candidate_duration() -> None:
    assert _candidate().duration_seconds == 30.0


def test_candidate_rejects_end_before_start() -> None:
    with pytest.raises(ValidationError):
        _candidate(start_seconds=40.0, end_seconds=10.0)


@pytest.mark.parametrize("score", [-0.1, 1.1])
def test_candidate_rejects_score_out_of_range(score: float) -> None:
    with pytest.raises(ValidationError):
        _candidate(score=score)


def test_candidate_requires_reasons() -> None:
    with pytest.raises(ValidationError):
        _candidate(reasons=[])


def test_candidate_is_immutable() -> None:
    with pytest.raises(ValidationError):
        _candidate().score = 0.1  # type: ignore[misc]


def test_media_info_holds_values() -> None:
    info = MediaInfo(duration_seconds=97.04, has_video=True)
    assert info.duration_seconds == 97.04
    assert info.has_video is True


@pytest.mark.parametrize("duration", [0.0, -1.0, float("nan"), float("inf")])
def test_media_info_rejects_unusable_duration(duration: float) -> None:
    with pytest.raises(ValidationError):
        MediaInfo(duration_seconds=duration, has_video=True)


def test_media_info_is_immutable() -> None:
    info = MediaInfo(duration_seconds=1.0, has_video=True)
    with pytest.raises(ValidationError):
        info.has_video = False  # type: ignore[misc]


def test_rendered_clip_holds_values() -> None:
    clip = RenderedClip(candidate=_candidate(), path=Path("out/a.mp4"))
    assert clip.path == Path("out/a.mp4")
    assert clip.candidate == _candidate()


def test_rendered_clip_is_immutable() -> None:
    clip = RenderedClip(candidate=_candidate(), path=Path("a.mp4"))
    with pytest.raises(ValidationError):
        clip.path = Path("b.mp4")  # type: ignore[misc]
