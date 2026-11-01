from pathlib import Path

import pytest

from clipper.core.config import Settings


def test_clips_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLIPPER_CLIPS_DIR", raising=False)
    assert Settings().clips_dir == Path("data/clips")


def test_clips_dir_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLIPPER_CLIPS_DIR", str(tmp_path))
    assert Settings().clips_dir == tmp_path


def test_labels_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLIPPER_LABELS_DIR", raising=False)
    assert Settings().labels_dir == Path("data/labels")


def test_labels_dir_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLIPPER_LABELS_DIR", str(tmp_path))
    assert Settings().labels_dir == tmp_path
