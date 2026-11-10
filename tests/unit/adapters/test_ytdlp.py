import subprocess
from pathlib import Path

import pytest

from clipper.adapters.download.ytdlp import (
    YtDlpClipLookup,
    YtDlpDownloader,
    YtDlpVideoInfoLookup,
)
from clipper.core.interfaces import ClipLookup, Downloader, VideoInfoLookup
from clipper.domain.errors import DownloadError
from clipper.domain.models import ClipInfo, VideoInfo, VideoSource

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


# Clip lookups. Constructed clip ID and JSON; the real output's shape was
# checked by hand (`-J` on a clips.twitch.tv URL gave title and duration).


def _fake_json_run(
    calls: list[tuple[list[str], dict[str, object]]],
    stdout: str,
    code: int = 0,
    stderr: str = "",
) -> object:
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, code, stdout, stderr)

    return run


def test_clip_lookup_satisfies_protocol() -> None:
    assert isinstance(YtDlpClipLookup(), ClipLookup)


@pytest.mark.usefixtures("on_path")
def test_clip_lookup_reads_title_and_duration_without_downloading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    stdout = '{"id": "1", "title": "Big moment \\u2728", "duration": 29.5}'
    monkeypatch.setattr(subprocess, "run", _fake_json_run(calls, stdout))

    info = YtDlpClipLookup().lookup("FunnyClip-x")

    assert info == ClipInfo(title="Big moment ✨", duration_seconds=29.5)
    command, kwargs = calls[0]
    assert command == [
        "yt-dlp",
        "--ignore-config",
        "--skip-download",
        "--dump-single-json",
        "https://clips.twitch.tv/FunnyClip-x",
    ]
    assert kwargs["capture_output"] is True


def test_clip_lookup_missing_program_raises() -> None:
    with pytest.raises(DownloadError, match="not found on PATH.*uv tool install"):
        YtDlpClipLookup(executable="definitely-not-yt-dlp").lookup("FunnyClip-x")


@pytest.mark.usefixtures("on_path")
def test_clip_lookup_nonzero_exit_reports_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stderr = "ERROR: [twitch:clips] FunnyClip-x: This clip is no longer available\n"
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(subprocess, "run", _fake_json_run(calls, "", 1, stderr))
    with pytest.raises(DownloadError, match=r"^yt-dlp failed \(exit 1\): .*no longer"):
        YtDlpClipLookup().lookup("FunnyClip-x")


@pytest.mark.usefixtures("on_path")
@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "not json",
        "[]",
        '{"title": "x"}',
        '{"title": "x", "duration": null}',
        '{"title": "x", "duration": 0}',
        '{"duration": 30}',
    ],
)
def test_clip_lookup_unusable_json_raises(
    monkeypatch: pytest.MonkeyPatch, stdout: str
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(subprocess, "run", _fake_json_run(calls, stdout))
    with pytest.raises(DownloadError, match="no usable title and duration for clip"):
        YtDlpClipLookup().lookup("FunnyClip-x")


# Video info. Constructed JSON; the fields were checked by hand on a Twitch
# VOD and a YouTube video (`-J --skip-download`, yt-dlp 2026.10.26).

_TWITCH = VideoSource(platform="twitch", video_id="v1234567890")


def test_video_info_satisfies_protocol() -> None:
    assert isinstance(YtDlpVideoInfoLookup(), VideoInfoLookup)


@pytest.mark.usefixtures("on_path")
def test_video_info_keeps_the_fields_without_downloading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    stdout = (
        '{"title": "A talk \u2728", "uploader": "The Show", "channel": "The Show",'
        ' "upload_date": "20261018", "duration": 7978, "description": "Links",'
        ' "formats": [{"url": "https://example.invalid/x"}]}'
    )
    monkeypatch.setattr(subprocess, "run", _fake_json_run(calls, stdout))

    info = YtDlpVideoInfoLookup().lookup(_SOURCE)

    assert info == VideoInfo(
        platform="youtube",
        video_id="abcDEF123_-",
        title="A talk ✨",
        channel="The Show",
        upload_date="20261018",
        duration_seconds=7978,
        description="Links",
    )
    command, kwargs = calls[0]
    assert command == [
        "yt-dlp",
        "--ignore-config",
        "--skip-download",
        "--no-playlist",
        "--dump-single-json",
        "https://www.youtube.com/watch?v=abcDEF123_-",
    ]
    assert kwargs["capture_output"] is True


@pytest.mark.usefixtures("on_path")
def test_a_twitch_vod_has_an_uploader_and_no_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    stdout = (
        '{"title": "WE HERE", "uploader": "streamer", "channel": null,'
        ' "upload_date": "20261102", "duration": 18463, "description": ""}'
    )
    monkeypatch.setattr(subprocess, "run", _fake_json_run(calls, stdout))

    info = YtDlpVideoInfoLookup().lookup(_TWITCH)

    assert (info.channel, info.description) == ("streamer", "")
    assert calls[0][0][-1] == "https://www.twitch.tv/videos/1234567890"


@pytest.mark.usefixtures("on_path")
def test_video_info_falls_back_to_the_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    stdout = '{"title": "x", "channel": "The Show"}'
    monkeypatch.setattr(subprocess, "run", _fake_json_run(calls, stdout))

    info = YtDlpVideoInfoLookup().lookup(_SOURCE)

    assert info.channel == "The Show"
    assert (info.upload_date, info.duration_seconds, info.description) == (
        None,
        None,
        "",
    )


@pytest.mark.usefixtures("on_path")
def test_video_info_nonzero_exit_reports_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stderr = "ERROR: [youtube] abcDEF123_-: Video unavailable\n"
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(subprocess, "run", _fake_json_run(calls, "", 1, stderr))
    with pytest.raises(DownloadError, match=r"^yt-dlp failed \(exit 1\): .*unavail"):
        YtDlpVideoInfoLookup().lookup(_SOURCE)


@pytest.mark.usefixtures("on_path")
@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "not json",
        "[]",
        '{"uploader": "u"}',
        '{"title": "x"}',
        '{"title": "", "uploader": "u"}',
        '{"title": "x", "uploader": "u", "duration": -1}',
    ],
)
def test_video_info_unusable_json_raises(
    monkeypatch: pytest.MonkeyPatch, stdout: str
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(subprocess, "run", _fake_json_run(calls, stdout))
    with pytest.raises(DownloadError, match="no usable title and channel for video"):
        YtDlpVideoInfoLookup().lookup(_SOURCE)
