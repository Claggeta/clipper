from pathlib import Path

import pytest

from clipper.adapters.results.manifest_json import MANIFEST_NAME, save_manifest
from clipper.domain.errors import ClipperError, ManifestError
from clipper.domain.models import ManifestClip, RenderManifest

# Constructed manifest, not the output of a real run.
_MANIFEST = RenderManifest(
    source="talk.mp4",
    analysis="data/output/talk.json",
    detector="heuristic",
    vertical=True,
    captions=True,
    titles=True,
    clips=(
        ManifestClip(
            rank=1,
            start_seconds=1.5,
            end_seconds=9.25,
            score=0.5,
            reasons=("tést",),
            file="talk_000001-000010.mp4",
            captions="words",
            title="Tïtle",
            hook="A hook.",
        ),
    ),
)


def test_save_writes_manifest_json_that_round_trips(tmp_path: Path) -> None:
    path = save_manifest(_MANIFEST, tmp_path / "talk")
    assert path == tmp_path / "talk" / MANIFEST_NAME
    assert RenderManifest.model_validate_json(path.read_bytes()) == _MANIFEST


def test_save_writes_utf8_without_bom_and_with_lf(tmp_path: Path) -> None:
    raw = save_manifest(_MANIFEST, tmp_path).read_bytes()
    assert "tést".encode() in raw
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in raw
    assert raw.endswith(b"}\n")


def test_save_replaces_an_earlier_manifest(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_NAME).write_text("old", encoding="utf-8")
    save_manifest(_MANIFEST, tmp_path)
    assert (tmp_path / MANIFEST_NAME).read_text(encoding="utf-8").startswith("{")


def test_unwritable_path_raises_manifest_error(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_NAME).mkdir()
    with pytest.raises(ManifestError, match="Cannot write manifest") as info:
        save_manifest(_MANIFEST, tmp_path)
    assert isinstance(info.value, ClipperError)
