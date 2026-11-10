from clipper.adapters.detection.windows import (
    MAX_CANDIDATE_SECONDS,
    PAUSE_SECONDS,
    STEP_SECONDS,
    WINDOW_SECONDS,
    extend_ends,
    select_top,
    sliding_windows,
)
from clipper.domain.models import ClipCandidate, Segment, Transcript, Word

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


def _worded(*spans: tuple[float, float]) -> Segment:
    words = tuple(Word(start_seconds=a, end_seconds=b, text="w") for a, b in spans)
    return Segment(
        start_seconds=spans[0][0], end_seconds=spans[-1][1], text="w", words=words
    )


def test_pause_and_maximum_match_adr_0023() -> None:
    assert (PAUSE_SECONDS, MAX_CANDIDATE_SECONDS) == (1.5, 90.0)


def test_end_moves_to_the_first_pause_after_it() -> None:
    transcript = _transcript(
        _worded((0.0, 5.0), (5.0, 10.0)),
        _worded((10.5, 12.0), (13.0, 14.0)),
        _worded((15.6, 17.0)),
    )

    # Gaps of 0.5 s and 1.0 s carry on; the 1.6 s gap after 14 s is a pause.
    assert extend_ends([_c(0, 10, 0.5)], transcript)[0].end_seconds == 14.0


def test_a_gap_of_exactly_the_pause_stops_the_end() -> None:
    transcript = _transcript(_worded((0.0, 10.0)), _worded((11.5, 12.0)))
    assert extend_ends([_c(0, 10, 0.5)], transcript)[0].end_seconds == 10.0


def test_end_runs_to_the_transcript_end_without_a_pause() -> None:
    transcript = _transcript(_worded((0.0, 10.0), (10.2, 20.0)), _worded((20.5, 30.0)))
    assert extend_ends([_c(0, 10, 0.5)], transcript)[0].end_seconds == 30.0


def test_segments_without_words_are_the_fallback() -> None:
    transcript = _transcript(_seg(0, 10), _seg(11, 20), _seg(25, 30))
    assert extend_ends([_c(0, 10, 0.5)], transcript)[0].end_seconds == 20.0


def test_end_never_passes_the_maximum_from_the_start() -> None:
    transcript = _transcript(*[_seg(i * 10.0, i * 10.0 + 10) for i in range(12)])

    # 80-90 ends at 90 s, the cap; 90-100 would pass it.
    assert extend_ends([_c(0, 40, 0.5)], transcript)[0].end_seconds == 90.0


def test_end_never_runs_into_a_later_chosen_candidate() -> None:
    transcript = _transcript(*[_seg(i * 10.0, i * 10.0 + 10) for i in range(10)])
    first, later = _c(0, 20, 0.5), _c(55, 60, 0.9)

    # The ranks keep their order; 40-50 is the last segment ending by 55 s.
    assert extend_ends([later, first], transcript) == [
        _c(55, 100, 0.9),
        _c(0, 50, 0.5),
    ]


def test_an_end_already_at_a_pause_and_the_start_stay() -> None:
    candidate = _c(3, 10, 0.5)
    transcript = _transcript(_seg(3, 10), _seg(20, 30))
    assert extend_ends([candidate], transcript) == [candidate]


def test_extend_ends_with_nothing_chosen() -> None:
    assert extend_ends([], _transcript(_seg(0, 10))) == []
