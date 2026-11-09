from pathlib import Path

from clipper.adapters.results.manifest_json import load_manifest
from clipper.adapters.results.review_files import JsonReviewFiles
from clipper.core.interfaces import ReviewFiles
from clipper.domain.models import LabeledVideo, RenderManifest

# Constructed labels and manifest, not the output of a real run.


def test_satisfies_protocol() -> None:
    assert isinstance(JsonReviewFiles(), ReviewFiles)


def test_missing_labels_file_is_none(tmp_path: Path) -> None:
    assert JsonReviewFiles().load_labels(tmp_path / "picks" / "talk.json") is None


def test_labels_and_manifest_round_trip(tmp_path: Path) -> None:
    files = JsonReviewFiles()
    labeled = LabeledVideo.model_validate(
        {
            "source": "talk.mp4",
            "labels": [{"start": "0:01:00", "end": "0:01:45", "verdict": "post"}],
            "rejected": [{"start": "0:05:00", "end": "0:05:40", "note": "dull"}],
        }
    )
    path = tmp_path / "picks" / "talk.json"

    files.save_labels(labeled, path)

    assert files.load_labels(path) == labeled

    manifest = RenderManifest(
        source="talk.mp4",
        analysis="a.json",
        detector=None,
        vertical=False,
        captions=False,
        titles=False,
        clips=(),
    )
    files.save_manifest(manifest, tmp_path / "talk")
    assert load_manifest(tmp_path / "talk") == manifest
