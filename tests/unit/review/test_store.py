from pathlib import Path

import pytest

from clipper.adapters.rendering.fake import FakeClipRenderer
from clipper.adapters.results.labels_json import load_labels
from clipper.adapters.results.review_files import JsonReviewFiles
from clipper.domain.errors import JudgingError, ReviewError
from clipper.domain.models import (
    ClipCandidate,
    ManifestClip,
    RenderManifest,
    Segment,
    Transcript,
    Word,
)
from clipper.review.store import RenderInputs, ReviewStore, SaveRequest

# Constructed manifest and transcript, not the output of a real run.

TRANSCRIPT = Transcript(
    segments=(
        Segment(
            start_seconds=12.0,
            end_seconds=14.0,
            text="Hello there.",
            words=(
                Word(start_seconds=12.0, end_seconds=12.6, text="Hello"),
                Word(start_seconds=12.7, end_seconds=13.4, text="there."),
            ),
        ),
    )
)


def _clip(rank: int, start: float, end: float, file: str) -> ManifestClip:
    return ManifestClip(
        rank=rank,
        start_seconds=start,
        end_seconds=end,
        score=0.5,
        reasons=("test",),
        file=file,
    )


def _manifest(*clips: ManifestClip, captions: bool = True) -> RenderManifest:
    return RenderManifest(
        source="talk.mp4",
        analysis="data/output/talk.json",
        detector="heuristic",
        vertical=True,
        captions=captions,
        titles=False,
        clips=clips,
    )


class Env:
    def __init__(
        self,
        tmp_path: Path,
        *clips: ManifestClip,
        renderer: FakeClipRenderer | None = None,
        captions: bool = True,
    ) -> None:
        self.folder = tmp_path / "clips" / "talk"
        self.folder.mkdir(parents=True)
        clips = clips or (_clip(1, 10.0, 40.0, "talk_1.mp4"),)
        for clip in clips:
            if clip.file is not None:
                (self.folder / clip.file).write_bytes(b"clip")
        self.labels_path = tmp_path / "labels" / "picks" / "talk.json"
        self.renderer = renderer or FakeClipRenderer()
        self.store = ReviewStore(
            self.folder,
            _manifest(*clips, captions=captions),
            None,
            self.labels_path,
            JsonReviewFiles(),
            RenderInputs(tmp_path / "talk.mp4", self.renderer, TRANSCRIPT),
        )

    def save(self, rank: int, start: float, end: float) -> ManifestClip:
        return self.store.save(rank, SaveRequest(start, end, None, None)).clip


def test_a_re_render_keeps_the_draft_span_so_a_second_save_finds_its_label(
    tmp_path: Path,
) -> None:
    env = Env(tmp_path)

    first = env.save(1, 9.0, 42.0)
    assert (first.start_seconds, first.end_seconds) == (9.0, 42.0)
    assert first.draft_span == (10.0, 40.0)
    assert first.captions == "words"

    second = env.save(1, 10.0, 40.0)
    assert second.file == "talk_000010-000040.mp4"
    assert second.draft_span == (10.0, 40.0)
    labels = load_labels(env.labels_path)
    assert [label.verdict for label in labels.labels] == ["post"]
    assert sorted(p.name for p in env.folder.iterdir()) == [
        "manifest.json",
        "talk_000010-000040.mp4",
    ]


def _titled(title: str | None) -> ManifestClip:
    return _clip(1, 10.0, 40.0, "talk_1.mp4").model_copy(
        update={"title": title, "hook": "A hook." if title else None}
    )


def test_the_first_save_keeps_the_models_title_and_later_ones_leave_it(
    tmp_path: Path,
) -> None:
    env = Env(tmp_path, _titled("$134 to fill a tank"))

    first = env.store.save(1, SaveRequest(10.0, 40.0, "Ron at the pump", None))
    second = env.store.save(1, SaveRequest(10.0, 40.0, "Ron shocked", None))

    assert first.clip.model_title == "$134 to fill a tank"
    assert (second.clip.title, second.clip.model_title) == (
        "Ron shocked",
        "$134 to fill a tank",
    )


def test_a_first_trash_keeps_the_models_title(tmp_path: Path) -> None:
    env = Env(tmp_path, _titled("$134 to fill a tank"))

    env.store.trash(1, None)
    clip = env.store.save(1, SaveRequest(10.0, 40.0, "Mine", None)).clip

    assert (clip.title, clip.model_title) == ("Mine", "$134 to fill a tank")


def test_no_model_title_is_kept_as_none(tmp_path: Path) -> None:
    env = Env(tmp_path, _titled(None))

    clip = env.store.save(1, SaveRequest(10.0, 40.0, "Typed", None)).clip

    assert (clip.title, clip.model_title) == ("Typed", None)


def test_add_saves_a_new_clip_ranked_after_the_drafts(tmp_path: Path) -> None:
    env = Env(tmp_path)

    result = env.store.add(SaveRequest(100.0, 130.0, "Missed moment", "funny"))

    clip = result.clip
    assert (clip.rank, clip.added, clip.reviewed) == (2, True, "saved")
    assert (clip.score, clip.reasons) == (0.0, ("added in review",))
    assert (clip.file, clip.error, clip.title) == (
        "talk_000140-000210.mp4",
        None,
        "Missed moment",
    )
    assert clip.draft_span == (100.0, 130.0)
    assert (env.folder / "talk_000140-000210.mp4").exists()
    [label] = load_labels(env.labels_path).labels
    assert (label.verdict, label.draft, label.note) == ("added", None, "funny")
    assert [c.rank for c in env.store.manifest.clips] == [1, 2]


def test_saving_an_added_clip_again_keeps_added_and_moves_it(tmp_path: Path) -> None:
    env = Env(tmp_path)
    env.store.add(SaveRequest(100.0, 130.0, None, None))

    clip = env.save(2, 95.0, 131.0)

    assert (clip.start_seconds, clip.end_seconds) == (95.0, 131.0)
    assert clip.draft_span == (95.0, 131.0)
    [label] = load_labels(env.labels_path).labels
    assert (label.verdict, label.start_seconds) == ("added", 95.0)
    assert sorted(p.name for p in env.folder.iterdir()) == [
        "manifest.json",
        "talk_000135-000211.mp4",
        "talk_1.mp4",
    ]


def test_an_add_over_a_label_writes_nothing(tmp_path: Path) -> None:
    env = Env(tmp_path)
    env.save(1, 10.0, 40.0)

    with pytest.raises(JudgingError, match="Overlaps the label"):
        env.store.add(SaveRequest(30.0, 60.0, None, None))

    assert [c.rank for c in env.store.manifest.clips] == [1]
    assert len(load_labels(env.labels_path).labels) == 1


def test_an_added_clip_whose_render_fails_records_the_error(tmp_path: Path) -> None:
    moved = ClipCandidate(
        start_seconds=100.0, end_seconds=130.0, score=0.0, reasons=("added in review",)
    )
    env = Env(tmp_path, renderer=FakeClipRenderer(fail_on=[moved]))

    result = env.store.add(SaveRequest(100.0, 130.0, None, None))

    assert result.render_error is not None
    assert (result.clip.file, result.clip.error) == (None, result.render_error)
    assert load_labels(env.labels_path).labels[0].verdict == "added"


def test_trash_removes_an_added_clip_outright(tmp_path: Path) -> None:
    env = Env(tmp_path)
    env.store.add(SaveRequest(100.0, 130.0, None, None))

    assert env.store.trash(2, "changed my mind") is None

    assert [c.rank for c in env.store.manifest.clips] == [1]
    labels = load_labels(env.labels_path)
    assert (labels.labels, labels.rejected) == ((), ())
    assert sorted(p.name for p in env.folder.iterdir()) == [
        "manifest.json",
        "talk_1.mp4",
    ]


def test_a_re_render_under_the_same_name_replaces_the_file(tmp_path: Path) -> None:
    env = Env(tmp_path, _clip(1, 10.2, 39.8, "talk_000010-000040.mp4"))

    clip = env.save(1, 10.1, 39.9)

    assert clip.file == "talk_000010-000040.mp4"
    assert (env.folder / clip.file).read_bytes() == b"fake clip"


def test_a_name_taken_by_another_clip_is_refused_before_anything_is_written(
    tmp_path: Path,
) -> None:
    env = Env(
        tmp_path,
        _clip(1, 10.0, 40.0, "talk_1.mp4"),
        _clip(2, 60.0, 90.0, "talk_000011-000040.mp4"),
    )

    with pytest.raises(JudgingError, match="is clip 2's"):
        env.save(1, 11.0, 40.0)
    assert not env.labels_path.exists()
    assert env.renderer.calls == []


def test_a_failed_render_of_a_clip_without_a_file_records_the_error(
    tmp_path: Path,
) -> None:
    moved = ClipCandidate(
        start_seconds=10.0, end_seconds=40.0, score=0.5, reasons=("test",)
    )
    env = Env(tmp_path, renderer=FakeClipRenderer(fail_on=[moved]))
    env.store.trash(1, None)

    result = env.store.save(1, SaveRequest(10.0, 40.0, None, None))

    assert result.render_error == "Fake render failed for talk_000010-000040.part.mp4"
    assert (result.clip.reviewed, result.clip.file) == ("saved", None)
    assert result.clip.error == result.render_error
    assert [p.name for p in env.folder.iterdir()] == ["manifest.json"]


def test_an_old_file_that_cannot_be_deleted_is_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = Env(tmp_path)
    real_unlink = Path.unlink

    def held_open(self: Path, missing_ok: bool = False) -> None:
        if self.name == "talk_1.mp4":
            raise PermissionError("in use")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", held_open)
    result = env.store.save(1, SaveRequest(9.0, 42.0, None, None))

    assert result.render_error is None
    assert result.warning == (
        "Re-rendered, but the old file talk_1.mp4 was not deleted: in use"
    )
    assert result.clip.file == "talk_000009-000042.mp4"


def test_without_captions_the_folder_re_renders_without_them(tmp_path: Path) -> None:
    env = Env(tmp_path, captions=False)

    clip = env.save(1, 9.0, 42.0)

    assert env.renderer.captions == [None]
    assert clip.captions is None


def test_a_span_without_speech_renders_without_captions(tmp_path: Path) -> None:
    env = Env(tmp_path, _clip(1, 100.0, 130.0, "talk_1.mp4"))

    clip = env.save(1, 100.0, 131.0)

    assert env.renderer.captions == [None]
    assert clip.captions is None


def test_a_file_that_cannot_be_deleted_leaves_the_manifest_and_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = Env(tmp_path)
    store = env.store
    real_unlink = Path.unlink

    def held_open(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError("in use by another process")

    monkeypatch.setattr(Path, "unlink", held_open)
    with pytest.raises(ReviewError, match="Cannot delete talk_1.mp4: in use"):
        store.trash(1, "dull")

    # The rejected span is written; the clip is not marked trashed yet.
    assert len(load_labels(env.labels_path).rejected)
    assert store.manifest.clips[0].reviewed is None

    monkeypatch.setattr(Path, "unlink", real_unlink)
    trashed = store.trash(1, "dull")
    assert trashed is not None
    assert trashed.reviewed == "trashed"
    assert len(store.labels.rejected if store.labels else ()) == 1


def test_trash_deletes_only_a_plain_file_name(tmp_path: Path) -> None:
    env = Env(tmp_path, _clip(1, 10.0, 40.0, "../talk_1.mp4"))

    with pytest.raises(ReviewError, match="not a file name"):
        env.store.trash(1, None)


def test_a_re_render_keeps_an_odd_old_file_name(tmp_path: Path) -> None:
    env = Env(tmp_path, _clip(1, 10.0, 40.0, "../talk_1.mp4"))

    result = env.store.save(1, SaveRequest(9.0, 42.0, None, None))

    assert (
        result.warning == "Re-rendered; not deleting '../talk_1.mp4': not a file name"
    )


def test_a_render_that_cannot_be_moved_into_place_keeps_the_old_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = Env(tmp_path)

    def blocked(source: Path, target: Path) -> None:
        raise PermissionError("in use")

    monkeypatch.setattr("os.replace", blocked)
    result = env.store.save(1, SaveRequest(9.0, 42.0, None, None))

    assert result.render_error == "Cannot replace talk_000009-000042.mp4: in use"
    assert result.clip.file == "talk_1.mp4"
    assert sorted(p.name for p in env.folder.iterdir()) == [
        "manifest.json",
        "talk_1.mp4",
    ]
