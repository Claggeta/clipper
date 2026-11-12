import subprocess
from pathlib import Path

import pytest

from clipper.adapters.media.remux import FfmpegRemuxer
from clipper.core.interfaces import Remuxer
from clipper.domain.errors import RemuxError

# ffmpeg is mocked; manual step D13 runs the real one.


def _patch_run(
    monkeypatch: pytest.MonkeyPatch, commands: list[list[str]], code: int = 0
) -> None:
    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        Path(command[-1]).write_bytes(b"remuxed")
        return subprocess.CompletedProcess(command, code, "", "" if not code else "bad")

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(subprocess, "run", fake_run)


def test_satisfies_protocol() -> None:
    assert isinstance(FfmpegRemuxer(), Remuxer)


def test_a_stream_copy_with_the_index_first_replaces_the_video(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "v1.mp4"
    video.write_bytes(b"original")
    commands: list[list[str]] = []
    _patch_run(monkeypatch, commands)

    FfmpegRemuxer().remux(video)

    [command] = commands
    assert command[command.index("-i") + 1] == str(video)
    assert command[command.index("-c") + 1] == "copy"
    assert command[command.index("-map") + 1] == "0"
    assert command[command.index("-movflags") + 1] == "+faststart"
    assert "-copyts" not in command
    assert command[-1] == str(tmp_path / "v1.part.mp4")
    assert video.read_bytes() == b"remuxed"
    assert not (tmp_path / "v1.part.mp4").exists()


def test_a_failed_remux_keeps_the_video_and_leaves_no_part_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "v1.mp4"
    video.write_bytes(b"original")
    _patch_run(monkeypatch, [], code=1)

    with pytest.raises(RemuxError, match="bad"):
        FfmpegRemuxer().remux(video)
    assert video.read_bytes() == b"original"
    assert not (tmp_path / "v1.part.mp4").exists()
