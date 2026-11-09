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
    assert store.trash(1, "dull").reviewed == "trashed"
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
