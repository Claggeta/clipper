import subprocess
from pathlib import Path

import pytest

from clipper.adapters.download.ytdlp import YtDlpDownloader
from clipper.core.interfaces import Downloader
from clipper.domain.errors import DownloadError
from clipper.domain.models import VideoSource

# Constructed ID and stderr; nothing here ran the real yt-dlp.
_SOURCE = VideoSource(platform="youtube", video_id="abcDEF123_-")


def _fake_run(
    calls: list[tuple[list[str], dict[str, object]]],
    code: int = 0,
    stderr: str = "",
    write: bool = True,
) -> object:
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        if write:
            folder = Path(command[command.index("-P") + 1])
            (folder / "abcDEF123_-.mp4").write_bytes(b"fake video")
        return subprocess.CompletedProcess(command, code, None, stderr)

    return run


@pytest.fixture
def on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/yt-dlp")


def test_satisfies_protocol() -> None:
    assert isinstance(YtDlpDownloader(), Downloader)


@pytest.mark.usefixtures("on_path")
def test_downloads_one_video_to_its_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls))
    folder = tmp_path / "videos"

    path = YtDlpDownloader().download(_SOURCE, folder)

    assert path == folder / "abcDEF123_-.mp4"
    command, kwargs = calls[0]
    assert command == [
        "yt-dlp",
        "--ignore-config",
        "--no-playlist",
        "--match-filter",
        "!is_live",
        "-S",
        "res:1080,ext:mp4:m4a",
        "--merge-output-format",
        "mp4",
        "--remux-video",
        "mp4",
        "-P",
        str(folder),
        "-o",
        "abcDEF123_-.%(ext)s",
        "https://www.youtube.com/watch?v=abcDEF123_-",
    ]
    # Progress on stdout reaches the console; only stderr is captured.
    assert "stdout" not in kwargs
    assert kwargs["stderr"] == subprocess.PIPE


def test_missing_program_raises(tmp_path: Path) -> None:
    downloader = YtDlpDownloader(executable="definitely-not-yt-dlp")
    with pytest.raises(DownloadError, match="not found on PATH.*uv tool install"):
        downloader.download(_SOURCE, tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.usefixtures("on_path")
def test_nonzero_exit_reports_the_last_stderr_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stderr = "WARNING: noise\nERROR: [youtube] abcDEF123_-: Video unavailable\n"
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, 1, stderr, write=False))
    with pytest.raises(DownloadError) as info:
        YtDlpDownloader().download(_SOURCE, tmp_path)
    assert str(info.value).startswith("yt-dlp failed (exit 1): WARNING: noise\n")
    assert str(info.value).endswith("Video unavailable")


@pytest.mark.usefixtures("on_path")
def test_exit_zero_without_a_file_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, write=False))
    with pytest.raises(DownloadError, match="exited 0 but wrote no file.*live"):
        YtDlpDownloader().download(_SOURCE, tmp_path)
