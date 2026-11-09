import pytest

from clipper.core.evaluation import (
    CUT_SHORT_SECONDS,
    HIT_FRACTION,
    boundary_gaps,
    evaluate,
    is_hit,
)
from clipper.domain.models import ClipCandidate, Label, LabeledVideo


def _c(start: float, end: float) -> ClipCandidate:
    # Constructed candidate; the reason text is arbitrary.
    return ClipCandidate(
        start_seconds=start, end_seconds=end, score=0.5, reasons=("test",)
    )


def _l(start: str, end: str) -> Label:
    # Constructed label, not a hand-labeled moment.
    return Label(start=start, end=end)


def _video(*labels: Label) -> LabeledVideo:
    return LabeledVideo(source="talk.mp4", labels=labels)


def test_threshold_is_half() -> None:
    assert HIT_FRACTION == 0.5


def test_candidate_inside_a_long_label_hits() -> None:
    assert is_hit(_c(60.0, 105.0), _l("0:50", "2:29"))


def test_label_inside_a_long_candidate_hits() -> None:
    assert is_hit(_c(0.0, 120.0), _l("0:10", "0:20"))


def test_overlap_of_exactly_half_the_shorter_range_hits() -> None:
    # Candidate 40-60 is the shorter (20 s); overlap with 0:50-2:00 is 10 s.
    assert is_hit(_c(40.0, 60.0), _l("0:50", "2:00"))


def test_overlap_just_under_half_misses() -> None:
    # Candidate 39.999-60 is 20.001 s; overlap is still 10 s, under half.
    assert not is_hit(_c(39.999, 60.0), _l("0:50", "2:00"))


def test_float_noise_does_not_move_the_boundary() -> None:
    assert is_hit(_c(40.0000000001, 60.0), _l("0:50", "2:00"))


def test_touching_ranges_do_not_hit() -> None:
    assert not is_hit(_c(50.0, 95.0), _l("0:00", "0:50"))


def test_disjoint_ranges_do_not_hit() -> None:
    assert not is_hit(_c(100.0, 145.0), _l("0:00", "0:50"))


def test_test_input_top_candidate_hits_the_second_label_not_the_first() -> None:
    # The saved top candidate for test_input.mp4 against the maintainer's first
    # two labels, worked out by hand: 17.57 s of overlap with 0:00-0:50 is
    # under half of the candidate's 41.35 s; 23.78 s with 0:50-2:29 is not.
    candidate = _c(32.43, 73.78)
    assert not is_hit(candidate, _l("0:00", "0:50"))
    assert is_hit(candidate, _l("0:50", "2:29"))


def test_evaluate_records_every_hitting_pair() -> None:
    labels = _video(_l("0:00", "0:30"), _l("0:30", "1:00"), _l("5:00", "6:00"))
    candidates = [_c(10.0, 50.0), _c(200.0, 240.0)]

    result = evaluate(labels, candidates)

    # Candidate 0 overlaps label 0 by 20 s and label 1 by 20 s; both are at
    # least half of the 30 s labels.
    assert result.hits == ((0, 0), (1, 0))
    # Candidate 0 matches only the earlier label; label 1 still counts for
    # recall, because recall asks whether any candidate hit it.
    assert result.matches == ((0, 0),)
    assert result.labels_hit == 2
    assert result.candidates_hit == 1
    assert result.recall == pytest.approx(2 / 3)
    assert result.precision == pytest.approx(1 / 2)
    assert result.source == "talk.mp4"
    assert result.candidates == tuple(candidates)


def test_two_candidates_on_one_label_count_the_label_once() -> None:
    result = evaluate(_video(_l("0:00", "2:00")), [_c(0.0, 45.0), _c(60.0, 105.0)])

    assert result.hits == ((0, 0), (0, 1))
    assert result.matches == ((0, 0),)
    assert result.labels_hit == 1
    assert result.candidates_hit == 2
    assert result.repeats == 1
    assert result.recall == 1.0
    assert result.precision == 0.5


def test_rank_order_decides_which_candidate_matches() -> None:
    # The list is in rank order, so the later-starting candidate ranked first
    # is the match and the other is the repeat.
    result = evaluate(_video(_l("0:00", "2:00")), [_c(60.0, 105.0), _c(0.0, 45.0)])
    assert result.matches == ((0, 0),)


def test_candidate_hitting_two_labels_claims_the_earlier() -> None:
    labels = _video(_l("0:00", "0:30"), _l("0:30", "1:00"))

    # Candidate 1 (30-60) hits only label 1, which candidate 0 left free.
    freed = evaluate(labels, [_c(10.0, 50.0), _c(30.0, 60.0)])
    # Candidate 1 (0-30) hits only label 0, which candidate 0 claimed.
    taken = evaluate(labels, [_c(10.0, 50.0), _c(0.0, 30.0)])

    assert freed.matches == ((0, 0), (1, 1))
    assert freed.precision == 1.0
    assert taken.matches == ((0, 0),)
    assert taken.repeats == 1


def test_no_labels_leaves_recall_undefined() -> None:
    result = evaluate(_video(), [_c(0.0, 45.0)])

    assert result.recall is None
    assert result.precision == 0.0


def test_no_candidates_leaves_precision_undefined() -> None:
    result = evaluate(_video(_l("0:00", "0:50")), [])

    assert result.precision is None
    assert result.recall == 0.0


def test_nothing_at_all() -> None:
    result = evaluate(_video(), [])

    assert result.hits == ()
    assert result.recall is None
    assert result.precision is None


# Judged labels (ADR 0012, V5 amendment).


def _judged(
    labels: list[dict[str, object]], rejected: list[dict[str, object]] | None = None
) -> LabeledVideo:
    # Constructed judged labels, not the maintainer's verdicts.
    return LabeledVideo.model_validate(
        {"source": "talk.mp4", "labels": labels, "rejected": rejected or []}
    )


def test_cut_short_tolerance_is_one_second() -> None:
    assert CUT_SHORT_SECONDS == 1.0


def test_labels_without_verdicts_are_not_judged() -> None:
    result = evaluate(_video(_l("0:00", "0:30")), [_c(0.0, 30.0), _c(100.0, 145.0)])

    assert not result.judged
    assert result.matches == ((0, 0),)
    assert result.on_rejected == ()
    assert result.candidates_unjudged == 0
    assert boundary_gaps(result) == []


def test_candidates_fall_on_a_label_a_rejected_span_or_neither() -> None:
    labeled = _judged(
        [{"start": "1:00", "end": "1:45", "verdict": "post"}],
        [{"start": "0:30", "end": "1:15"}, {"start": "5:00", "end": "5:45"}],
    )
    # 0: on the label (and half on the first rejected span); 1: rejected;
    # 2: on nothing.
    candidates = [_c(60.0, 105.0), _c(300.0, 345.0), _c(600.0, 645.0)]

    result = evaluate(labeled, candidates)

    assert result.judged
    assert result.matches == ((0, 0),)
    assert result.on_rejected == (1,)
    assert result.candidates_unjudged == 1


def test_a_repeat_is_on_a_label_not_rejected() -> None:
    labeled = _judged(
        [{"start": "1:00", "end": "2:00", "verdict": "added"}],
        [{"start": "1:30", "end": "2:15"}],
    )
    result = evaluate(labeled, [_c(60.0, 105.0), _c(90.0, 135.0)])

    assert result.repeats == 1
    assert result.on_rejected == ()
    assert result.candidates_unjudged == 0


def test_boundary_gaps_are_label_minus_candidate_in_rank_order() -> None:
    labeled = _judged(
        [
            {
                "start": "1:00",
                "end": "1:48.5",
                "verdict": "fix",
                "draft": {"start": "1:00", "end": "1:45"},
            },
            {"start": "4:58", "end": "5:40", "verdict": "post"},
        ]
    )
    # Ranked: the later moment first.
    result = evaluate(labeled, [_c(300.0, 345.0), _c(60.0, 105.0)])

    gaps = boundary_gaps(result)

    assert [(g.candidate_index, g.start, g.end) for g in gaps] == [
        (0, -2.0, -5.0),
        (1, 0.0, 3.5),
    ]
    assert [(g.ends_early, g.starts_late) for g in gaps] == [
        (False, True),
        (True, False),
    ]


@pytest.mark.parametrize(("end", "early"), [("1:46", False), ("1:46.001", True)])
def test_ends_early_only_past_the_tolerance(end: str, early: bool) -> None:
    labeled = _judged([{"start": "1:00", "end": end, "verdict": "post"}])

    [gap] = boundary_gaps(evaluate(labeled, [_c(60.0, 105.0)]))

    assert gap.ends_early is early
