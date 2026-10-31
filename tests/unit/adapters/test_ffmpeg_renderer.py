import subprocess
from pathlib import Path

import pytest

from clipper.adapters.rendering.ffmpeg import FfmpegClipRenderer
from clipper.core.interfaces import ClipRenderer
from clipper.domain.errors import ClipRenderError
from clipper.domain.models import ClipCandidate

_CANDIDATE = ClipCandidate(
    start_seconds=32.5, end_seconds=73.25, score=0.5, reasons=("pace",)
)


def _patch_run(
    monkeypatch: pytest.MonkeyPatch,
    captured: list[list[str]],
    *,
    code: int = 0,
    stderr: str = "",
    write: Path | None = None,
) -> None:
    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured.append(command)
        if write is not None:
            write.write_bytes(b"partial")
        return subprocess.CompletedProcess(command, code, "", stderr)

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(subprocess, "run", fake_run)


@pytest.fixture
def video(tmp_path: Path) -> Path:
    path = tmp_path / "v.mp4"
    path.write_bytes(b"x")
    return path


def test_satisfies_protocol() -> None:
    assert isinstance(FfmpegClipRenderer(), ClipRenderer)


def test_missing_input_raises(tmp_path: Path) -> None:
    with pytest.raises(ClipRenderError, match="not found"):
        FfmpegClipRenderer().render(
            tmp_path / "nope.mp4", _CANDIDATE, tmp_path / "c.mp4"
        )


def test_missing_ffmpeg_raises(video: Path, tmp_path: Path) -> None:
    renderer = FfmpegClipRenderer(executable="definitely-not-ffmpeg")
    with pytest.raises(ClipRenderError, match="PATH"):
        renderer.render(video, _CANDIDATE, tmp_path / "c.mp4")


def test_output_equal_to_input_is_refused(video: Path) -> None:
    with pytest.raises(ClipRenderError, match="overwrite"):
        FfmpegClipRenderer().render(video, _CANDIDATE, video)


def test_success_returns_rendered_clip_and_creates_parent(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "clips" / "c.mp4"
    _patch_run(monkeypatch, [])

    clip = FfmpegClipRenderer().render(video, _CANDIDATE, out)

    assert clip.path == out
    assert clip.candidate == _CANDIDATE
    assert out.parent.is_dir()


def test_success_builds_expected_command(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "c.mp4"
    captured: list[list[str]] = []
    _patch_run(monkeypatch, captured)

    FfmpegClipRenderer().render(video, _CANDIDATE, out)

    assert captured == [
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-ss",
            "32.500",
            "-i",
            str(video),
            "-t",
            "40.750",
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(out),
        ]
    ]


def test_seek_is_before_input_and_banner_is_hidden(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[list[str]] = []
    _patch_run(monkeypatch, captured)

    FfmpegClipRenderer().render(video, _CANDIDATE, tmp_path / "c.mp4")

    command = captured[0]
    assert command.index("-ss") < command.index("-i")
    assert "-hide_banner" in command
    assert command[command.index("-loglevel") + 1] == "error"


def test_float_noise_is_formatted_as_fixed_milliseconds(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = ClipCandidate(
        start_seconds=0.1 + 0.2, end_seconds=1e3, score=0.5, reasons=("pace",)
    )
    captured: list[list[str]] = []
    _patch_run(monkeypatch, captured)

    FfmpegClipRenderer().render(video, candidate, tmp_path / "c.mp4")

    command = captured[0]
    assert command[command.index("-ss") + 1] == "0.300"
    assert command[command.index("-t") + 1] == "999.700"


def test_nonzero_exit_raises_with_cause_and_exit_code(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_run(monkeypatch, [], code=1, stderr="noise\n\nUnknown encoder 'libx264'\n")

    with pytest.raises(ClipRenderError) as excinfo:
        FfmpegClipRenderer().render(video, _CANDIDATE, tmp_path / "c.mp4")

    assert "exit 1" in str(excinfo.value)
    assert "Unknown encoder 'libx264'" in str(excinfo.value)


def test_failure_removes_partial_output(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "c.mp4"
    _patch_run(monkeypatch, [], code=1, stderr="boom", write=out)

    with pytest.raises(ClipRenderError):
        FfmpegClipRenderer().render(video, _CANDIDATE, out)

    assert not out.exists()
