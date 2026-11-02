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


def test_llm_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLIPPER_LLM_URL", raising=False)
    monkeypatch.delenv("CLIPPER_LLM_MODEL", raising=False)
    settings = Settings()
    assert settings.llm_url == "http://127.0.0.1:11434"
    assert settings.llm_model is None


def test_llm_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLIPPER_LLM_URL", "http://localhost:9999")
    monkeypatch.setenv("CLIPPER_LLM_MODEL", "some-model:tag")
    settings = Settings()
    assert settings.llm_url == "http://localhost:9999"
    assert settings.llm_model == "some-model:tag"
