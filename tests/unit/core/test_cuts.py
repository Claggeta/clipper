import pytest

from clipper.core.cuts import (
    CUT_PADDING_SECONDS,
    TimeMap,
    check_cuts,
    cut_crop,
    find_pauses,
)
from clipper.domain.errors import ClipRenderError
from clipper.domain.models import CropPath, Segment, Transcript, Word

# Constructed transcripts, not real speech.


def _words(*spans: tuple[float, float]) -> Segment:
    words = tuple(Word(start_seconds=a, end_seconds=b, text="w") for a, b in spans)
    return Segment(
        start_seconds=spans[0][0], end_seconds=spans[-1][1], text="w", words=words
    )


def _transcript(*segments: Segment) -> Transcript:
    return Transcript(source="v.mp4", language="en", segments=segments)


def test_pauses_between_words_are_cut_less_padding() -> None:
    transcript = _transcript(_words((10.0, 10.5), (10.6, 11.0), (12.0, 12.4)))
    assert find_pauses(transcript, 9.0, 20.0) == (
        (11.0 + CUT_PADDING_SECONDS, 12.0 - CUT_PADDING_SECONDS),
    )


def test_short_gaps_and_the_edges_are_not_cut() -> None:
    transcript = _transcript(_words((10.0, 10.5), (11.0, 11.4)))
    # 0.5 s between words is under the minimum; the silence before the first
    # word and after the last stays.
    assert find_pauses(transcript, 5.0, 20.0) == ()


def test_gaps_span_segments_and_ignore_speech_outside_the_clip() -> None:
    transcript = _transcript(
        _words((1.0, 2.0)),
        _words((10.0, 10.5)),
        _words((13.0, 13.5)),
        _words((30.0, 31.0)),
    )
    assert find_pauses(transcript, 9.0, 20.0) == ((10.65, 12.85),)


def test_a_segment_without_words_counts_as_speech_throughout() -> None:
    wordless = Segment(start_seconds=11.0, end_seconds=15.0, text="hmm")
    transcript = _transcript(_words((10.0, 10.5)), wordless, _words((16.0, 16.5)))
    # 10.5 to 11.0 is under the minimum; the gap after the wordless segment is not.
    assert find_pauses(transcript, 9.0, 20.0) == ((15.15, 15.85),)


def test_a_pause_whose_cut_would_leave_the_clip_is_kept() -> None:
    transcript = _transcript(_words((10.0, 10.5), (12.0, 12.5)))
    assert find_pauses(transcript, 10.0, 11.0) == ()


def test_check_cuts_accepts_ordered_cuts_inside_the_span() -> None:
    check_cuts(((11.0, 12.0), (13.0, 14.0)), 10.0, 20.0)
    check_cuts((), 10.0, 20.0)


@pytest.mark.parametrize(
    "cuts",
    [((10.0, 11.0),), ((19.0, 20.0),), ((12.0, 11.0),), ((11.0, 13.0), (12.0, 14.0))],
)
def test_check_cuts_refuses_bad_cuts(cuts: tuple[tuple[float, float], ...]) -> None:
    with pytest.raises(ClipRenderError, match="must lie inside the clip"):
        check_cuts(cuts, 10.0, 20.0)


def test_time_map_shifts_times_past_each_cut() -> None:
    time_map = TimeMap(10.0, 30.0, ((12.0, 14.0), (20.0, 25.0)))
    assert time_map.output(11.0) == 1.0
    assert time_map.output(13.0) is None
    assert time_map.output(15.0) == 3.0
    assert time_map.output(26.0) == 9.0
    assert time_map.clamped(13.0) == 2.0
    assert time_map.clamped(22.0) == 8.0
    assert time_map.duration == 13.0
    assert time_map.kept() == [(0.0, 2.0), (4.0, 10.0), (15.0, 20.0)]


def test_time_map_without_cuts_is_a_shift() -> None:
    time_map = TimeMap(10.0, 30.0)
    assert time_map.output(12.5) == 2.5
    assert time_map.kept() == [(0.0, 20.0)]


def test_crop_moves_follow_the_cuts() -> None:
    crop = CropPath(width=600, height=1080, points=((0.0, 100), (3.0, 300), (8.0, 500)))
    moved = cut_crop(crop, TimeMap(10.0, 30.0, ((12.0, 14.0), (16.0, 17.0))))
    # 3.0 s is inside the first cut, so it moves where the cut ends (2.0 s);
    # 8.0 s comes after both cuts, 3 s earlier.
    assert moved.points == ((0.0, 100), (2.0, 300), (5.0, 500))
    assert cut_crop(crop, TimeMap(10.0, 30.0)) is crop
