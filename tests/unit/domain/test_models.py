from pathlib import Path

import pytest
from pydantic import ValidationError

from clipper.domain.models import (
    ClipCandidate,
    ClipInfo,
    ClipLink,
    ClipTitle,
    Evaluation,
    Label,
    LabeledVideo,
    ManifestClip,
    MediaInfo,
    RenderedClip,
    Segment,
    Transcript,
    VideoSource,
    Word,
    format_timestamp,
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


def _w(start: float, end: float, text: str = "w") -> Word:
    return Word(start_seconds=start, end_seconds=end, text=text)


def test_word_allows_zero_length() -> None:
    assert _w(1.0, 1.0).end_seconds == 1.0


def test_word_rejects_end_before_start() -> None:
    with pytest.raises(ValidationError):
        _w(2.0, 1.0)


def test_segment_words_default_to_empty() -> None:
    assert Segment(start_seconds=0.0, end_seconds=1.0, text="hi").words == ()


def test_segment_without_words_key_loads_from_older_json() -> None:
    segment = Segment.model_validate_json(
        '{"start_seconds": 0.0, "end_seconds": 1.0, "text": "hi"}'
    )
    assert segment.words == ()


def test_segment_accepts_ordered_words_inside_it() -> None:
    words = (_w(1.0, 1.5, "a"), _w(1.5, 1.5, "b"), _w(1.6, 2.0, "c"))
    segment = Segment(start_seconds=1.0, end_seconds=2.0, text="a b c", words=words)
    assert segment.words == words


@pytest.mark.parametrize(
    "words",
    [
        (_w(0.5, 1.2),),  # starts before the segment
        (_w(1.5, 2.5),),  # ends after the segment
        (_w(1.0, 1.6), _w(1.5, 1.8)),  # overlap
        (_w(1.6, 1.8), _w(1.0, 1.2)),  # out of order
    ],
)
def test_segment_rejects_words_out_of_order_or_outside(words: tuple[Word, ...]) -> None:
    with pytest.raises(ValidationError):
        Segment(start_seconds=1.0, end_seconds=2.0, text="x", words=words)


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


# Labels are hand-written, so the times below are constructed input.


def _label(start: object, end: object, note: str | None = None) -> Label:
    return Label(start=start, end=end, note=note)


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("0:00", 0.0),
        ("0:50", 50.0),
        ("2:29", 149.0),
        ("0:49.78", 49.78),
        ("75:10", 4510.0),
        ("1:02:03", 3723.0),
        ("1:02:03.5", 3723.5),
    ],
)
def test_label_parses_timestamps(text: str, seconds: float) -> None:
    assert _label(text, "9:00:00").start_seconds == pytest.approx(seconds)


@pytest.mark.parametrize(
    "value",
    [
        0.5,
        50,
        True,
        None,
        "50",
        "0.50",
        "0:5",
        "0:60",
        "1:60:00",
        "1:2:03",
        " 0:50",
        "0:50 ",
        "-0:50",
        "0:50.",
        "",
        "١:٢٣",
    ],
)
def test_label_rejects_other_time_forms(value: object) -> None:
    with pytest.raises(ValidationError, match="M:SS"):
        _label(value, "9:00:00")


def test_label_duration_and_note() -> None:
    label = _label("3:48", "4:35", "Ring the bell")
    assert label.duration_seconds == 47.0
    assert label.note == "Ring the bell"


def test_label_note_is_optional() -> None:
    assert _label("0:00", "0:50").note is None


@pytest.mark.parametrize("end", ["0:50", "0:49"])
def test_label_rejects_end_not_after_start(end: str) -> None:
    with pytest.raises(ValidationError, match="end must be after start"):
        _label("0:50", end)


def test_label_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError, match="end_second"):
        Label.model_validate({"start": "0:00", "end_second": "0:50"})


def test_label_rejects_seconds_field_names() -> None:
    with pytest.raises(ValidationError):
        Label.model_validate({"start_seconds": "0:00", "end_seconds": "0:50"})


def test_label_is_immutable() -> None:
    label = _label("0:00", "0:50")
    with pytest.raises(ValidationError):
        label.note = "changed"  # type: ignore[misc]


def _video(*labels: Label) -> LabeledVideo:
    return LabeledVideo(source="talk.mp4", labels=labels)


def test_labeled_video_allows_touching_labels() -> None:
    video = _video(_label("0:00", "0:50"), _label("0:50", "2:29"))
    assert [label.end_seconds for label in video.labels] == [50.0, 149.0]


def test_labeled_video_allows_no_labels() -> None:
    assert _video().labels == ()


def test_labeled_video_rejects_out_of_order_labels() -> None:
    with pytest.raises(ValidationError, match="labels.1 starts before labels.0 ends"):
        _video(_label("2:30", "3:47"), _label("0:00", "0:50"))


def test_labeled_video_rejects_overlapping_labels() -> None:
    with pytest.raises(ValidationError, match="labels.2 starts before labels.1 ends"):
        _video(_label("0:00", "0:50"), _label("0:50", "2:29"), _label("2:00", "3:00"))


def test_labeled_video_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError, match="video"):
        LabeledVideo.model_validate({"source": "a.mp4", "labels": [], "video": "x"})


# Judged labels (ADR 0012, V5 amendment), constructed.


def _judged(**fields: object) -> Label:
    return Label.model_validate({"start": "1:00", "end": "1:45", **fields})


def test_label_without_a_verdict_has_none() -> None:
    label = _label("0:00", "0:50")
    assert (label.verdict, label.draft) == (None, None)


@pytest.mark.parametrize("verdict", ["post", "added"])
def test_post_and_added_labels_have_no_draft(verdict: str) -> None:
    assert _judged(verdict=verdict).verdict == verdict
    with pytest.raises(ValidationError, match='only a "fix" label has a "draft"'):
        _judged(verdict=verdict, draft={"start": "1:00", "end": "1:40"})


def test_fix_label_keeps_the_draft_span() -> None:
    label = _judged(verdict="fix", draft={"start": "1:00", "end": "1:40"})
    assert label.draft is not None
    assert (label.draft.start_seconds, label.draft.end_seconds) == (60.0, 100.0)


def test_fix_label_needs_a_draft() -> None:
    with pytest.raises(ValidationError, match='needs the draft\'s span in "draft"'):
        _judged(verdict="fix")


def test_fix_label_must_move_the_span() -> None:
    with pytest.raises(ValidationError, match="must differ from its draft"):
        _judged(verdict="fix", draft={"start": "1:00", "end": "1:45.0001"})


def test_draft_without_a_verdict_is_rejected() -> None:
    with pytest.raises(ValidationError, match='only a "fix" label has a "draft"'):
        _judged(draft={"start": "1:00", "end": "1:40"})


@pytest.mark.parametrize(
    "fields",
    [
        {"verdict": "keep"},
        {"verdict": "fix", "draft": {"start": "1:40", "end": "1:00"}},
        {"verdict": "fix", "draft": {"start": "1:00", "end": "1:40", "x": "y"}},
    ],
)
def test_bad_verdict_or_draft_is_rejected(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _judged(**fields)


def _judged_video(
    labels: list[dict[str, object]], rejected: list[dict[str, object]]
) -> LabeledVideo:
    return LabeledVideo.model_validate(
        {"source": "v1.mp4", "labels": labels, "rejected": rejected}
    )


def test_judged_video_with_rejected_spans() -> None:
    video = _judged_video(
        [{"start": "1:00", "end": "1:45", "verdict": "post"}],
        [
            {"start": "0:30", "end": "1:15", "note": "the other half"},
            {"start": "5:00", "end": "5:45"},
            {"start": "5:10", "end": "5:55"},
        ],
    )
    assert video.judged
    assert [span.start_seconds for span in video.rejected] == [30.0, 300.0, 310.0]
    assert video.rejected[0].note == "the other half"


def test_rejected_spans_alone_make_a_judged_file() -> None:
    assert _judged_video([], [{"start": "0:30", "end": "1:15"}]).judged


def test_a_file_without_verdicts_is_not_judged() -> None:
    assert not _video(_label("0:00", "0:50")).judged
    assert not _video().judged


def test_verdicts_on_some_labels_only_are_rejected() -> None:
    with pytest.raises(ValidationError, match="labels.1 has no verdict"):
        _judged_video(
            [
                {"start": "1:00", "end": "1:45", "verdict": "post"},
                {"start": "2:00", "end": "2:45"},
            ],
            [],
        )


def test_rejected_spans_need_verdicts() -> None:
    with pytest.raises(ValidationError, match="need a verdict on every label"):
        _judged_video(
            [{"start": "1:00", "end": "1:45"}], [{"start": "0:30", "end": "1:15"}]
        )


def test_rejected_spans_must_be_ordered_by_start() -> None:
    with pytest.raises(ValidationError, match="rejected.1 starts before rejected.0"):
        _judged_video(
            [], [{"start": "5:00", "end": "5:45"}, {"start": "0:30", "end": "1:15"}]
        )


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (0.0, "0:00:00"),
        (50.0, "0:00:50"),
        (16545.0, "4:35:45"),
        (70.5, "0:01:10.5"),
        (49.78, "0:00:49.78"),
        (3599.9996, "1:00:00"),
        (1.0004, "0:00:01"),
    ],
)
def test_format_timestamp(seconds: float, text: str) -> None:
    assert format_timestamp(seconds) == text


@pytest.mark.parametrize("seconds", [0.0, 50.0, 70.5, 16545.25, 49.78])
def test_format_timestamp_round_trips_through_label(seconds: float) -> None:
    label = _label(format_timestamp(seconds), format_timestamp(seconds + 1))
    assert label.start_seconds == pytest.approx(seconds)


def test_clip_link_needs_a_positive_end() -> None:
    vod = VideoSource(platform="twitch", video_id="v1")
    with pytest.raises(ValidationError):
        ClipLink(line=1, clip_id="a", vod=vod, end_seconds=0)


@pytest.mark.parametrize("duration", [0, -1, float("inf"), float("nan")])
def test_clip_info_needs_a_finite_positive_length(duration: float) -> None:
    with pytest.raises(ValidationError):
        ClipInfo(title="x", duration_seconds=duration)


# Evaluations below are constructed, not produced by `clipper eval`.


def _evaluation(
    hits: tuple[tuple[int, int], ...], matches: tuple[tuple[int, int], ...] = ()
) -> Evaluation:
    return Evaluation(
        source="talk.mp4",
        labels=(_label("0:00", "0:50"), _label("0:50", "2:29")),
        candidates=(
            _candidate(start_seconds=0, end_seconds=45),
            _candidate(start_seconds=60, end_seconds=105),
            _candidate(start_seconds=200, end_seconds=245),
        ),
        hits=hits,
        matches=matches,
    )


def test_evaluation_counts_distinct_labels_and_candidates() -> None:
    evaluation = _evaluation(((0, 0), (1, 0), (1, 1)), ((0, 0), (1, 1)))
    assert evaluation.labels_hit == 2
    assert evaluation.candidates_hit == 2
    assert evaluation.candidates_matched == 2
    assert evaluation.repeats == 0
    assert evaluation.recall == 1.0
    assert evaluation.precision == pytest.approx(2 / 3)


def test_evaluation_precision_counts_matches_not_hits() -> None:
    evaluation = _evaluation(((1, 0), (1, 1)), ((1, 0),))
    assert evaluation.candidates_hit == 2
    assert evaluation.candidates_matched == 1
    assert evaluation.repeats == 1
    assert evaluation.precision == pytest.approx(1 / 3)


def test_evaluation_with_no_hits() -> None:
    evaluation = _evaluation(())
    assert (evaluation.labels_hit, evaluation.candidates_hit) == (0, 0)
    assert evaluation.recall == 0.0
    assert evaluation.precision == 0.0


@pytest.mark.parametrize(
    ("hits", "matches", "message"),
    [
        (((0, 0),), ((1, 0),), "is not a hit"),
        (((1, 0), (1, 1)), ((1, 0), (1, 1)), "label is matched more than once"),
        (((0, 0), (1, 0)), ((0, 0), (1, 0)), "candidate is matched more than once"),
    ],
)
def test_evaluation_rejects_matches_that_are_not_one_to_one_hits(
    hits: tuple[tuple[int, int], ...],
    matches: tuple[tuple[int, int], ...],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _evaluation(hits, matches)


@pytest.mark.parametrize(
    ("hit", "message"),
    [((2, 0), "label 2"), ((-1, 0), "label -1"), ((0, 3), "candidate 3")],
)
def test_evaluation_rejects_hits_out_of_range(
    hit: tuple[int, int], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _evaluation((hit,))


def _with(judged: bool, on_rejected: tuple[int, ...]) -> Evaluation:
    plain = _evaluation(((0, 0),), ((0, 0),))
    return Evaluation(
        source=plain.source,
        labels=plain.labels,
        candidates=plain.candidates,
        hits=plain.hits,
        matches=plain.matches,
        judged=judged,
        on_rejected=on_rejected,
    )


def test_evaluation_counts_unjudged_only_for_judged_labels() -> None:
    assert _evaluation(((0, 0),), ((0, 0),)).candidates_unjudged == 0
    assert _with(True, (1,)).candidates_unjudged == 1
    assert _with(True, ()).candidates_unjudged == 2


@pytest.mark.parametrize(
    ("judged", "on_rejected", "message"),
    [
        (True, (3,), "candidate 3, which does not exist"),
        (True, (0,), "candidate 0 hits a label"),
        (False, (1,), "on_rejected needs judged labels"),
    ],
)
def test_evaluation_rejects_bad_on_rejected(
    judged: bool, on_rejected: tuple[int, ...], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _with(judged, on_rejected)


def test_evaluation_is_immutable() -> None:
    evaluation = _evaluation(())
    with pytest.raises(ValidationError):
        evaluation.source = "other.mp4"  # type: ignore[misc]


def _entry(**fields: object) -> dict[str, object]:
    # Constructed manifest entry.
    base: dict[str, object] = {
        "rank": 1,
        "start_seconds": 1.0,
        "end_seconds": 2.0,
        "score": 0.5,
        "reasons": ("test",),
    }
    return base | fields


def test_manifest_clip_with_a_file() -> None:
    clip = ManifestClip.model_validate(_entry(file="a.mp4", captions="words"))
    assert (clip.file, clip.captions, clip.error) == ("a.mp4", "words", None)


def test_manifest_clip_with_an_error() -> None:
    clip = ManifestClip.model_validate(_entry(error="boom"))
    assert (clip.file, clip.captions) == (None, None)


@pytest.mark.parametrize(
    "fields", [{}, {"file": "a.mp4", "error": "boom"}], ids=["neither", "both"]
)
def test_manifest_clip_needs_exactly_one_of_file_and_error(
    fields: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="exactly one of file and error"):
        ManifestClip.model_validate(_entry(**fields))


def test_manifest_clip_rejects_unknown_caption_kinds_and_rank_zero() -> None:
    with pytest.raises(ValidationError):
        ManifestClip.model_validate(_entry(file="a.mp4", captions="srt"))
    with pytest.raises(ValidationError):
        ManifestClip.model_validate(_entry(file="a.mp4", rank=0))


def test_manifest_clip_with_a_title_and_hook() -> None:
    clip = ManifestClip.model_validate(_entry(file="a.mp4", title="T", hook="H"))
    assert (clip.title, clip.hook) == ("T", "H")


@pytest.mark.parametrize(
    "fields",
    [
        {"file": "a.mp4", "title": "Typed in review"},
        {"file": "a.mp4", "hook": "H"},
        {"error": "boom", "title": "T", "hook": "H"},
    ],
    ids=["title alone", "hook alone", "on a failed clip"],
)
def test_manifest_clip_title_and_hook_are_independent(
    fields: dict[str, object],
) -> None:
    # The review page edits the title alone (ADR 0018, review amendment).
    ManifestClip.model_validate(_entry(**fields))


@pytest.mark.parametrize("fields", [{}, {"error": "boom"}], ids=["clean", "failed"])
def test_a_trashed_clip_has_no_file(fields: dict[str, object]) -> None:
    clip = ManifestClip.model_validate(_entry(reviewed="trashed", **fields))
    assert clip.file is None

    with pytest.raises(ValidationError, match="a trashed clip has no file"):
        ManifestClip.model_validate(_entry(reviewed="trashed", file="a.mp4"))


def test_draft_span_defaults_to_the_clips_own() -> None:
    clip = ManifestClip.model_validate(_entry(file="a.mp4"))
    assert clip.draft_span == (1.0, 2.0)

    moved = ManifestClip.model_validate(
        _entry(
            file="a.mp4",
            start_seconds=0.5,
            end_seconds=4.0,
            draft_start_seconds=1.0,
            draft_end_seconds=2.0,
        )
    )
    assert moved.draft_span == (1.0, 2.0)


def test_draft_span_fields_go_together() -> None:
    with pytest.raises(ValidationError, match="must be set together"):
        ManifestClip.model_validate(_entry(file="a.mp4", draft_start_seconds=1.0))


def test_a_saved_clip_still_needs_a_file_or_an_error() -> None:
    assert ManifestClip.model_validate(_entry(file="a.mp4", reviewed="saved"))
    with pytest.raises(ValidationError, match="exactly one of file and error"):
        ManifestClip.model_validate(_entry(reviewed="saved"))


def test_clip_title_is_stripped() -> None:
    title = ClipTitle(title="  A title ", hook=" A hook.  ")
    assert (title.title, title.hook) == ("A title", "A hook.")


@pytest.mark.parametrize(
    ("title", "hook", "message"),
    [
        ("  ", "hook", "at least 1 character"),
        ("title", "", "at least 1 character"),
        ("two\nlines", "hook", "must be one line"),
        ("title", "carriage\rreturn", "must be one line"),
    ],
)
def test_clip_title_rejects_empty_or_multiline_text(
    title: str, hook: str, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        ClipTitle(title=title, hook=hook)


def test_clip_title_is_immutable() -> None:
    title = ClipTitle(title="t", hook="h")
    with pytest.raises(ValidationError):
        title.title = "u"  # type: ignore[misc]


def test_a_clips_file_is_its_first_variant() -> None:
    from clipper.domain.models import ClipVariant, ManifestClip

    base = {"rank": 1, "start_seconds": 0, "end_seconds": 30, "score": 0.5}
    fit = ClipVariant(style="fit", file="a.mp4")
    face = ClipVariant(style="face", file="a.face.mp4")
    clip = ManifestClip(**base, reasons=("x",), file="a.mp4", variants=(fit, face))
    assert clip.variants == (fit, face)
    with pytest.raises(ValueError, match="first variant"):
        ManifestClip(**base, reasons=("x",), file="a.face.mp4", variants=(fit, face))
    assert ManifestClip(**base, reasons=("x",), file="a.mp4").variants == ()
