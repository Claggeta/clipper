from pathlib import Path

import pytest

from clipper.core.judging import (
    check_judged_labels,
    drop_clip,
    is_own_label,
    judged_labels_path,
    mark_clip,
    next_rank,
    remove_judgment,
    save_judgment,
    trash_judgment,
)
from clipper.domain.errors import ClipperError, JudgingError, LabelsError
from clipper.domain.models import LabeledVideo, ManifestClip, RenderManifest

# Constructed clips and labels, not the output of a real run. Draft spans
# carry more decimals than a labels file keeps, as real drafts do.


def _clip(rank: int, start: float, end: float) -> ManifestClip:
    return ManifestClip(
        rank=rank,
        start_seconds=start,
        end_seconds=end,
        score=0.5,
        reasons=("test",),
        file=f"talk_{rank}.mp4",
    )


ONE = _clip(1, 100.0004, 145.2)
TWO = _clip(2, 300.0, 340.0)


def test_unchanged_span_is_post() -> None:
    labeled = save_judgment(None, "talk.mp4", ONE, 100.0004, 145.2, "good")

    [label] = labeled.labels
    assert (label.verdict, label.draft, label.note) == ("post", None, "good")
    assert labeled.source == "talk.mp4"
    assert is_own_label(label, ONE)
    assert not is_own_label(label, TWO)


def test_moved_span_is_fix_with_the_draft() -> None:
    labeled = save_judgment(None, "talk.mp4", ONE, 99.5, 150.0, None)

    [label] = labeled.labels
    assert label.verdict == "fix"
    assert label.draft is not None
    assert (label.draft.start_seconds, label.draft.end_seconds) == (100.0, 145.2)
    assert is_own_label(label, ONE)


def test_a_change_under_a_millisecond_is_still_post() -> None:
    [label] = save_judgment(None, "t.mp4", ONE, 100.0001, 145.2, None).labels
    assert label.verdict == "post"


def test_saving_again_replaces_the_label_and_keeps_others_in_order() -> None:
    labeled = save_judgment(None, "t.mp4", TWO, 300.0, 340.0, None)
    labeled = save_judgment(labeled, "t.mp4", ONE, 90.0, 150.0, None)
    labeled = save_judgment(labeled, "t.mp4", ONE, 100.0004, 145.2, "again")

    assert [(lb.verdict, lb.note) for lb in labeled.labels] == [
        ("post", "again"),
        ("post", None),
    ]


def test_an_overlap_of_half_the_shorter_span_is_refused() -> None:
    labeled = save_judgment(None, "t.mp4", TWO, 300.0, 340.0, None)

    # 280-320 overlaps 300-340 by 20 s, half of the shorter 40 s.
    with pytest.raises(JudgingError, match=r"0:05:00-0:05:40 \(post\) by half") as info:
        save_judgment(labeled, "t.mp4", ONE, 280.0, 320.0, None)
    assert isinstance(info.value, ClipperError)

    # A span inside the other overlaps it by all of the shorter one.
    with pytest.raises(JudgingError):
        save_judgment(labeled, "t.mp4", ONE, 310.0, 330.0, None)


def test_a_smaller_overlap_is_saved_as_a_neighbour() -> None:
    labeled = save_judgment(None, "t.mp4", TWO, 300.0, 340.0, None)

    # 260-310 overlaps by 10 s, a quarter of the shorter 40 s.
    labeled = save_judgment(labeled, "t.mp4", ONE, 260.0, 310.0, None)

    assert [(lb.start_seconds, lb.end_seconds) for lb in labeled.labels] == [
        (260.0, 310.0),
        (300.0, 340.0),
    ]
    # Touching is not overlapping either.
    assert len(save_judgment(labeled, "t.mp4", ONE, 100.0, 300.0, None).labels) == 2


def test_an_added_label_belongs_only_to_an_added_clip() -> None:
    labeled = LabeledVideo.model_validate(
        {
            "source": "t.mp4",
            "labels": [{"start": "0:01:40", "end": "0:02:25.2", "verdict": "added"}],
        }
    )
    added = _clip(2, 100.0, 145.2).model_copy(update={"added": True})

    assert not is_own_label(labeled.labels[0], _clip(1, 100.0, 145.2))
    assert is_own_label(labeled.labels[0], added)


def test_an_added_clip_is_saved_as_added_and_removed_without_a_trace() -> None:
    added = _clip(2, 100.0, 145.2).model_copy(update={"added": True})

    labeled = save_judgment(None, "t.mp4", added, 99.0, 150.0, None)
    [label] = labeled.labels
    assert (label.verdict, label.draft) == ("added", None)

    moved = added.model_copy(
        update={"draft_start_seconds": 99.0, "draft_end_seconds": 150.0}
    )
    emptied = remove_judgment(labeled, "t.mp4", moved)
    assert (emptied.labels, emptied.rejected) == ((), ())


def test_next_rank_and_drop_clip() -> None:
    manifest = RenderManifest(
        source="t.mp4",
        analysis="a.json",
        detector=None,
        vertical=False,
        captions=False,
        titles=False,
        clips=(ONE, TWO),
    )
    assert next_rank(manifest) == 3
    assert next_rank(manifest.model_copy(update={"clips": ()})) == 1
    assert drop_clip(manifest, 1).clips == (TWO,)


def test_trash_replaces_a_label_with_a_rejected_span() -> None:
    labeled = save_judgment(None, "t.mp4", ONE, 90.0, 150.0, None)
    labeled = save_judgment(labeled, "t.mp4", TWO, 300.0, 340.0, None)

    labeled = trash_judgment(labeled, "t.mp4", ONE, "dull")
    labeled = trash_judgment(labeled, "t.mp4", ONE, "dull, twice")

    assert [lb.start_seconds for lb in labeled.labels] == [300.0]
    assert [(r.start_seconds, r.note) for r in labeled.rejected] == [
        (100.0, "dull, twice")
    ]


def test_save_after_trash_removes_the_rejected_span() -> None:
    labeled = trash_judgment(None, "t.mp4", ONE, None)
    labeled = save_judgment(labeled, "t.mp4", ONE, 100.0004, 145.2, None)

    assert labeled.rejected == ()
    assert len(labeled.labels) == 1


def test_mark_clip_changes_one_clip_and_validates_it() -> None:
    manifest = RenderManifest(
        source="t.mp4",
        analysis="a.json",
        detector=None,
        vertical=False,
        captions=False,
        titles=False,
        clips=(ONE, TWO),
    )

    marked = mark_clip(manifest, 2, reviewed="trashed", file=None)

    assert marked.clips[0] == ONE
    assert (marked.clips[1].reviewed, marked.clips[1].file) == ("trashed", None)
    with pytest.raises(ValueError, match="a trashed clip has no file"):
        mark_clip(manifest, 1, reviewed="trashed")


def test_judged_labels_path() -> None:
    assert judged_labels_path(Path("data/labels"), "v123.mp4") == Path(
        "data/labels/picks/v123.json"
    )


def test_check_judged_labels() -> None:
    judged = trash_judgment(None, "talk.mp4", ONE, None)
    check_judged_labels(judged, "talk.mp4", Path("p.json"))

    with pytest.raises(LabelsError, match="is for 'talk.mp4', but the drafts"):
        check_judged_labels(judged, "other.mp4", Path("p.json"))

    unjudged = LabeledVideo.model_validate(
        {"source": "talk.mp4", "labels": [{"start": "0:01", "end": "0:02"}]}
    )
    with pytest.raises(LabelsError, match="labels without verdicts"):
        check_judged_labels(unjudged, "talk.mp4", Path("p.json"))
    check_judged_labels(
        LabeledVideo(source="talk.mp4", labels=()), "talk.mp4", Path("p.json")
    )
