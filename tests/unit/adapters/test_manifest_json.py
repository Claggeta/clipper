from pathlib import Path

import pytest

from clipper.adapters.results.manifest_json import (
    MANIFEST_NAME,
    check_manifest_source,
    load_manifest,
    save_manifest,
)
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


def test_load_reads_a_saved_manifest(tmp_path: Path) -> None:
    save_manifest(_MANIFEST, tmp_path)
    assert load_manifest(tmp_path) == _MANIFEST


def test_load_without_a_manifest_raises(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="Manifest not found"):
        load_manifest(tmp_path)


def test_load_reports_invalid_json(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_NAME).write_text("{", encoding="utf-8")
    with pytest.raises(ManifestError, match="Invalid JSON in manifest") as info:
        load_manifest(tmp_path)
    assert "Invalid JSON:" not in str(info.value)


def test_load_names_the_bad_field(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_NAME).write_text('{"source": "a.mp4"}', encoding="utf-8")
    with pytest.raises(ManifestError, match="is not a valid manifest: analysis"):
        load_manifest(tmp_path)


def test_load_reports_an_unreadable_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_manifest(_MANIFEST, tmp_path)

    def deny(self: Path) -> bytes:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_bytes", deny)
    with pytest.raises(ManifestError, match="Cannot read manifest"):
        load_manifest(tmp_path)


def test_source_check_compares_names(tmp_path: Path) -> None:
    check_manifest_source(_MANIFEST, tmp_path / "talk.mp4")
    with pytest.raises(ManifestError, match="Manifest is for 'talk.mp4'"):
        check_manifest_source(_MANIFEST, tmp_path / "other.mp4")
