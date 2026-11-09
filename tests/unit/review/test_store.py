from pathlib import Path

import pytest

from clipper.adapters.results.labels_json import load_labels
from clipper.adapters.results.review_files import JsonReviewFiles
from clipper.domain.errors import ReviewError
from clipper.domain.models import ManifestClip, RenderManifest
from clipper.review.store import ReviewStore

# Constructed manifest, not the output of a real run.


def _manifest(file: str) -> RenderManifest:
    clip = ManifestClip(
        rank=1,
        start_seconds=10.0,
        end_seconds=40.0,
        score=0.5,
        reasons=("test",),
        file=file,
    )
    return RenderManifest(
        source="talk.mp4",
        analysis="data/output/talk.json",
        detector="heuristic",
        vertical=True,
        captions=True,
        titles=False,
        clips=(clip,),
    )


def _store(tmp_path: Path, file: str = "talk_1.mp4") -> ReviewStore:
    folder = tmp_path / "clips" / "talk"
    folder.mkdir(parents=True)
    (folder / "talk_1.mp4").write_bytes(b"clip")
    return ReviewStore(
        folder,
        _manifest(file),
        None,
        tmp_path / "labels" / "picks" / "talk.json",
        JsonReviewFiles(),
    )


def test_a_file_that_cannot_be_deleted_leaves_the_manifest_and_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    real_unlink = Path.unlink

    def held_open(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError("in use by another process")

    monkeypatch.setattr(Path, "unlink", held_open)
    with pytest.raises(ReviewError, match="Cannot delete talk_1.mp4: in use"):
        store.trash(1, "dull")

    # The rejected span is written; the clip is not marked trashed yet.
    assert len(load_labels(tmp_path / "labels" / "picks" / "talk.json").rejected)
    assert store.manifest.clips[0].reviewed is None

    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert store.trash(1, "dull").reviewed == "trashed"
    assert len(store.labels.rejected if store.labels else ()) == 1


def test_trash_deletes_only_a_plain_file_name(tmp_path: Path) -> None:
    store = _store(tmp_path, file="../talk_1.mp4")

    with pytest.raises(ReviewError, match="not a file name"):
        store.trash(1, None)
    assert (tmp_path / "clips" / "talk" / "talk_1.mp4").exists()
