from clipper.adapters.detection.windows import (
    STEP_SECONDS,
    WINDOW_SECONDS,
    select_top,
    sliding_windows,
)
from clipper.domain.models import ClipCandidate, Segment, Transcript

# All fixtures here are constructed.


def _seg(start: float, end: float) -> Segment:
    return Segment(start_seconds=start, end_seconds=end, text="words")


def _transcript(*segments: Segment) -> Transcript:
    return Transcript(segments=segments)


def _c(start: float, end: float, score: float) -> ClipCandidate:
    return ClipCandidate(
        start_seconds=start, end_seconds=end, score=score, reasons=("test",)
    )


def test_window_and_step_match_adr_0014() -> None:
    assert (WINDOW_SECONDS, STEP_SECONDS) == (45.0, 15.0)


def test_empty_transcript_gives_no_windows() -> None:
    assert sliding_windows(_transcript()) == []


def test_windows_hold_whole_segments_only() -> None:
    segments = [_seg(i * 10.0, i * 10.0 + 10) for i in range(10)]

    windows = sliding_windows(_transcript(*segments))

    # From 0 s, a 45 s window holds the segments ending by 45 s: 0-10 to 30-40.
    assert windows[0] == tuple(segments[:4])
    assert all(w[-1].end_seconds - w[0].start_seconds <= 45 for w in windows)


def test_segment_longer_than_the_window_is_in_no_window() -> None:
    long = _seg(10.0, 60.0)

    windows = sliding_windows(_transcript(_seg(0.0, 10.0), long, _seg(60.0, 70.0)))

    assert windows
    assert all(long not in w for w in windows)


def test_identical_windows_from_adjacent_steps_are_kept_once() -> None:
    first, second = _seg(0.0, 10.0), _seg(100.0, 110.0)

    # Steps from 75 s and 90 s both hold only the second segment.
    assert sliding_windows(_transcript(first, second)) == [(first,), (second,)]


def test_windows_start_one_step_apart() -> None:
    segments = [_seg(i * 5.0, i * 5.0 + 5) for i in range(20)]

    windows = sliding_windows(_transcript(*segments), 10.0, 20.0)

    assert [w[0].start_seconds for w in windows] == [0.0, 20.0, 40.0, 60.0, 80.0]


def test_select_ranks_by_score() -> None:
    low, high, mid = _c(0, 10, 0.1), _c(20, 30, 0.9), _c(40, 50, 0.5)
    assert select_top([low, high, mid], 5) == [high, mid, low]


def test_select_breaks_ties_by_earlier_start() -> None:
    late, early = _c(40, 50, 0.5), _c(0, 10, 0.5)
    assert select_top([late, early], 5) == [early, late]


def test_select_skips_candidates_overlapping_a_chosen_one() -> None:
    best, overlapping, apart = _c(0, 45, 0.9), _c(30, 75, 0.8), _c(60, 105, 0.7)
    assert select_top([best, overlapping, apart], 5) == [best, apart]


def test_select_keeps_touching_candidates() -> None:
    first, touching = _c(0, 45, 0.9), _c(45, 90, 0.8)
    assert select_top([first, touching], 5) == [first, touching]


def test_select_stops_at_top_n() -> None:
    candidates = [_c(i * 50.0, i * 50.0 + 45, 0.9 - i / 10) for i in range(5)]
    assert select_top(candidates, 2) == candidates[:2]


def test_select_returns_fewer_when_there_are_fewer() -> None:
    only = _c(0, 10, 0.5)
    assert select_top([only], 5) == [only]
    assert select_top([], 5) == []
