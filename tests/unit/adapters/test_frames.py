import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from clipper.adapters.media.frames import FfmpegFrameExtractor
from clipper.core.interfaces import FrameExtractor
from clipper.domain.errors import ClipperError, FrameError


def _video(tmp_path: Path) -> Path:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    return video


def _run(
    returncode: int, stderr: str, captured: list[list[str]], write: bool
) -> Callable[..., subprocess.CompletedProcess[str]]:
    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured.append(command)
        if write:
            Path(command[-1]).write_bytes(b"jpeg")
        return subprocess.CompletedProcess(command, returncode, "", stderr)

    return fake_run


def test_satisfies_protocol() -> None:
    assert isinstance(FfmpegFrameExtractor(), FrameExtractor)


def test_missing_input_raises(tmp_path: Path) -> None:
    with pytest.raises(FrameError, match="not found") as info:
        FfmpegFrameExtractor().extract(tmp_path / "nope.mp4", 1.0, tmp_path / "f.jpg")
    assert isinstance(info.value, ClipperError)


def test_missing_ffmpeg_raises(tmp_path: Path) -> None:
    extractor = FfmpegFrameExtractor(executable="definitely-not-ffmpeg")
    with pytest.raises(FrameError, match="PATH"):
        extractor.extract(_video(tmp_path), 1.0, tmp_path / "f.jpg")


def test_success_seeks_before_the_input_and_saves_one_small_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = _video(tmp_path)
    out = tmp_path / "frames" / "12.jpg"
    captured: list[list[str]] = []
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(subprocess, "run", _run(0, "", captured, write=True))

    assert FfmpegFrameExtractor().extract(video, 12.0, out) == out

    command = captured[0]
    assert command[0] == "ffmpeg"
    assert "-hide_banner" in command
    assert command[command.index("-loglevel") + 1] == "error"
    assert command[command.index("-ss") + 1] == "12.000"
    assert command.index("-ss") < command.index("-i")
    assert command[command.index("-i") + 1] == str(video)
    assert command[command.index("-frames:v") + 1] == "1"
    assert command[command.index("-vf") + 1] == "scale=-2:72"
    assert command[-1] == str(out)


def test_nonzero_exit_raises_with_the_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(subprocess, "run", _run(1, "bad codec", [], write=False))

    with pytest.raises(FrameError, match=r"exit 1\): bad codec"):
        FfmpegFrameExtractor().extract(_video(tmp_path), 1.0, tmp_path / "f.jpg")


def test_success_without_a_file_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(subprocess, "run", _run(0, "", [], write=False))

    with pytest.raises(FrameError, match="No frame at 99.500 s"):
        FfmpegFrameExtractor().extract(_video(tmp_path), 99.5, tmp_path / "f.jpg")
