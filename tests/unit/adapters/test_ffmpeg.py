import subprocess
from pathlib import Path

import pytest

from clipper.adapters.media.ffmpeg import FfmpegAudioExtractor
from clipper.core.interfaces import AudioExtractor
from clipper.domain.errors import AudioExtractionError


def test_satisfies_protocol() -> None:
    assert isinstance(FfmpegAudioExtractor(), AudioExtractor)


def test_missing_input_raises(tmp_path: Path) -> None:
    with pytest.raises(AudioExtractionError, match="not found"):
        FfmpegAudioExtractor().extract(tmp_path / "nope.mp4", tmp_path / "a.wav")


def test_missing_ffmpeg_raises(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    extractor = FfmpegAudioExtractor(executable="definitely-not-ffmpeg")
    with pytest.raises(AudioExtractionError, match="PATH"):
        extractor.extract(video, tmp_path / "a.wav")


def test_success_builds_expected_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    out = tmp_path / "out" / "a.wav"
    captured: list[list[str]] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert FfmpegAudioExtractor().extract(video, out) == out
    assert "16000" in captured[0]
    assert out.parent.is_dir()


def test_nonzero_exit_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "bad codec")

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(AudioExtractionError, match="bad codec"):
        FfmpegAudioExtractor().extract(video, tmp_path / "a.wav")
