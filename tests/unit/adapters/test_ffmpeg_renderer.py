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


class _Run:
    """Records each ffmpeg call and what was on disk in its working directory."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, code: int = 0) -> None:
        self.commands: list[list[str]] = []
        self.cwds: list[Path] = []
        self.srt: list[bytes] = []

        def fake_run(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            cwd = kwargs["cwd"]
            assert isinstance(cwd, Path)
            self.commands.append(command)
            self.cwds.append(cwd)
            self.srt.append((cwd / "clip.srt").read_bytes())
            return subprocess.CompletedProcess(command, code, "", "boom")

        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
        monkeypatch.setattr(subprocess, "run", fake_run)


_SRT = "1\n00:00:00,000 --> 00:00:01,000\ncafé “quoted”\n"


def test_no_captions_means_no_filter_and_no_cwd(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwds: list[object] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        cwds.append(kwargs.get("cwd"))
        assert "-vf" not in command
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(subprocess, "run", fake_run)

    FfmpegClipRenderer().render(video, _CANDIDATE, tmp_path / "a.mp4")
    FfmpegClipRenderer().render(video, _CANDIDATE, tmp_path / "b.mp4", captions="")

    assert cwds == [None, None]


def test_captions_add_subtitles_filter_before_video_codec(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _Run(monkeypatch)

    FfmpegClipRenderer().render(video, _CANDIDATE, tmp_path / "c.mp4", captions=_SRT)

    command = run.commands[0]
    vf = command[command.index("-vf") + 1]
    assert vf.startswith("subtitles=clip.srt:charenc=UTF-8:force_style='")
    assert command.index("-vf") < command.index("-c:v")
    assert command[command.index("-loglevel") + 1] == "error"
    assert "-hide_banner" in command


def test_captions_file_is_utf8_without_bom_or_crlf(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _Run(monkeypatch)

    FfmpegClipRenderer().render(video, _CANDIDATE, tmp_path / "c.mp4", captions=_SRT)

    assert run.srt == [_SRT.encode("utf-8")]
    assert not run.srt[0].startswith(b"\xef\xbb\xbf")
    assert b"\r" not in run.srt[0]


def test_captions_run_in_temp_dir_with_absolute_paths(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _Run(monkeypatch)
    monkeypatch.chdir(tmp_path)

    FfmpegClipRenderer().render(
        Path(video.name), _CANDIDATE, Path("rel") / "c.mp4", captions=_SRT
    )

    command = run.commands[0]
    assert run.cwds[0] != tmp_path
    assert Path(command[command.index("-i") + 1]).is_absolute()
    assert Path(command[-1]) == (tmp_path / "rel" / "c.mp4").resolve()


def test_captions_returns_the_callers_output_path(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Run(monkeypatch)
    monkeypatch.chdir(tmp_path)

    clip = FfmpegClipRenderer().render(video, _CANDIDATE, Path("c.mp4"), captions=_SRT)

    assert clip.path == Path("c.mp4")


def test_captions_temp_dir_is_removed_after_success(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _Run(monkeypatch)

    FfmpegClipRenderer().render(video, _CANDIDATE, tmp_path / "c.mp4", captions=_SRT)

    assert not run.cwds[0].exists()


def test_captions_temp_dir_is_removed_after_failure(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _Run(monkeypatch, code=1)

    with pytest.raises(ClipRenderError, match="boom"):
        FfmpegClipRenderer().render(
            video, _CANDIDATE, tmp_path / "c.mp4", captions=_SRT
        )

    assert not run.cwds[0].exists()
