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


def _failing_run(stderr: str, captured: list[list[str]] | None = None) -> object:
    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if captured is not None:
            captured.append(command)
        return subprocess.CompletedProcess(command, 1, "", stderr)

    return fake_run


def test_error_message_carries_cause_and_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    stderr = "Stream #0:0: Video: h264\n\nv.mp4: Invalid data found\n"
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(subprocess, "run", _failing_run(stderr))

    with pytest.raises(AudioExtractionError) as excinfo:
        FfmpegAudioExtractor().extract(video, tmp_path / "a.wav")

    assert "exit 1" in str(excinfo.value)
    assert "v.mp4: Invalid data found" in str(excinfo.value)


def test_error_message_keeps_last_lines_of_long_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    stderr = "\n".join(f"line {i}" for i in range(30)) + "\nthe real cause\n"
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(subprocess, "run", _failing_run(stderr))

    with pytest.raises(AudioExtractionError) as excinfo:
        FfmpegAudioExtractor().extract(video, tmp_path / "a.wav")

    assert "the real cause" in str(excinfo.value)
    assert "line 29" in str(excinfo.value)
    assert "line 0" not in str(excinfo.value)


def test_command_suppresses_banner_and_info_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    captured: list[list[str]] = []
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(subprocess, "run", _failing_run("boom", captured))

    with pytest.raises(AudioExtractionError):
        FfmpegAudioExtractor().extract(video, tmp_path / "a.wav")

    assert "-hide_banner" in captured[0]
    assert captured[0][captured[0].index("-loglevel") + 1] == "error"
