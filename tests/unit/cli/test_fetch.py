from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from clipper.adapters.download.ytdlp import YtDlpDownloader, YtDlpVideoInfoLookup
from clipper.cli.context import AppContext
from clipper.cli.main import app
from clipper.core.config import Settings
from clipper.domain.errors import DownloadError, RemuxError
from clipper.domain.models import MediaInfo, VideoInfo, VideoSource

# Constructed IDs and messages; nothing here downloads.
_URL = "https://www.youtube.com/watch?v=abcDEF123_-&list=PLxyz"


class FakeDownloader:
    def __init__(self, error: str | None = None) -> None:
        self.calls: list[tuple[VideoSource, Path]] = []
        self._error = error

    def download(self, source: VideoSource, folder: Path) -> Path:
        self.calls.append((source, folder))
        if self._error:
            raise DownloadError(self._error)
        path = folder / f"{source.video_id}.mp4"
        folder.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake video")
        return path


class FakeVideoInfo:
    def __init__(self, error: str | None = None) -> None:
        self.calls: list[VideoSource] = []
        self._error = error

    def lookup(self, source: VideoSource) -> VideoInfo:
        self.calls.append(source)
        if self._error:
            raise DownloadError(self._error)
        return VideoInfo(
            platform=source.platform,
            video_id=source.video_id,
            title="A video",
            channel="A channel",
        )


class FakeProbe:
    """Every file starts at `start` seconds."""

    def __init__(self, start: float = 0.0) -> None:
        self.start = start

    def probe(self, media_path: Path) -> MediaInfo:
        return MediaInfo(
            duration_seconds=100.0, has_video=True, start_seconds=self.start
        )


class FakeRemuxer:
    def __init__(self, probe: FakeProbe, error: str | None = None) -> None:
        self.calls: list[Path] = []
        self._probe = probe
        self._error = error

    def remux(self, video_path: Path) -> None:
        self.calls.append(video_path)
        if self._error:
            raise RemuxError(self._error)
        self._probe.start = 0.0


@pytest.fixture
def videos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "videos"
    monkeypatch.setenv("CLIPPER_VIDEOS_DIR", str(folder))
    return folder


def _fetch(
    url: str,
    downloader: FakeDownloader,
    info: FakeVideoInfo | None = None,
    probe: FakeProbe | None = None,
    remuxer: FakeRemuxer | None = None,
) -> Result:
    lookup = info or FakeVideoInfo()
    media = probe or FakeProbe()
    remux = remuxer or FakeRemuxer(media)
    context = AppContext(
        downloader=lambda settings: downloader,
        video_info=lambda settings: lookup,
        probe=lambda: media,
        remuxer=lambda: remux,
    )
    return CliRunner().invoke(app, ["fetch", url], obj=context)


def test_fetch_saves_one_video_by_id(videos: Path) -> None:
    downloader = FakeDownloader()
    result = _fetch(_URL, downloader)
    assert result.exit_code == 0, result.output
    assert downloader.calls == [
        (VideoSource(platform="youtube", video_id="abcDEF123_-"), videos)
    ]
    assert f"Saved: {videos / 'abcDEF123_-.mp4'}" in result.output


def test_an_existing_file_is_not_fetched_again(videos: Path) -> None:
    videos.mkdir()
    (videos / "v1234567890.mp4").write_bytes(b"earlier")
    downloader = FakeDownloader()
    result = _fetch("https://www.twitch.tv/videos/1234567890", downloader)
    assert result.exit_code == 0, result.output
    assert downloader.calls == []
    assert f"Already fetched: {videos / 'v1234567890.mp4'}" in result.output


def test_a_refused_url_downloads_nothing(videos: Path) -> None:
    downloader = FakeDownloader()
    result = _fetch("https://www.youtube.com/playlist?list=PLxyz", downloader)
    assert result.exit_code == 1
    assert "Error: Playlist URLs are not fetched" in result.output
    assert downloader.calls == []
    assert not videos.exists()


def test_a_download_failure_is_an_error(videos: Path) -> None:
    downloader = FakeDownloader(error="yt-dlp failed (exit 1): ERROR: [x] bad")
    result = _fetch(_URL, downloader)
    assert result.exit_code == 1
    assert "Error: yt-dlp failed (exit 1): ERROR: [x] bad" in result.output


def test_help_states_the_terms() -> None:
    result = CliRunner().invoke(app, ["fetch", "--help"])
    assert result.exit_code == 0
    assert "against their terms of service" in result.output


def test_the_default_context_builds_the_ytdlp_downloader() -> None:
    assert isinstance(AppContext().downloader(Settings()), YtDlpDownloader)


def test_fetch_saves_the_video_info_beside_it(videos: Path) -> None:
    info = FakeVideoInfo()
    result = _fetch(_URL, FakeDownloader(), info)
    assert result.exit_code == 0, result.output
    assert info.calls == [VideoSource(platform="youtube", video_id="abcDEF123_-")]
    saved = VideoInfo.model_validate_json(
        (videos / "abcDEF123_-.info.json").read_bytes()
    )
    assert (saved.title, saved.channel) == ("A video", "A channel")
    assert f"Video info: {videos / 'abcDEF123_-.info.json'}" in result.output


def test_a_reused_file_gets_its_video_info_once(videos: Path) -> None:
    videos.mkdir()
    (videos / "v1234567890.mp4").write_bytes(b"earlier")
    info = FakeVideoInfo()
    url = "https://www.twitch.tv/videos/1234567890"

    first = _fetch(url, FakeDownloader(), info)
    second = _fetch(url, FakeDownloader(), info)

    assert first.exit_code == second.exit_code == 0
    assert (videos / "v1234567890.info.json").is_file()
    assert len(info.calls) == 1
    assert "Video info:" not in second.output


def test_a_failed_video_info_lookup_is_a_warning(videos: Path) -> None:
    info = FakeVideoInfo(error="yt-dlp failed (exit 1): ERROR: [x] gone")
    result = _fetch(_URL, FakeDownloader(), info)
    assert result.exit_code == 0, result.output
    assert (videos / "abcDEF123_-.mp4").is_file()
    assert not (videos / "abcDEF123_-.info.json").exists()
    assert (
        "Warning: no video info saved: yt-dlp failed (exit 1): ERROR: [x] gone"
        in result.output
    )


def test_a_failed_download_looks_up_no_video_info(videos: Path) -> None:
    info = FakeVideoInfo()
    result = _fetch(_URL, FakeDownloader(error="yt-dlp failed (exit 1): x"), info)
    assert result.exit_code == 1
    assert info.calls == []


def test_the_default_context_builds_the_ytdlp_video_info_lookup() -> None:
    assert isinstance(AppContext().video_info(Settings()), YtDlpVideoInfoLookup)


def test_a_download_that_starts_late_is_remuxed(videos: Path) -> None:
    probe = FakeProbe(start=64.0)
    remuxer = FakeRemuxer(probe)
    result = _fetch(_URL, FakeDownloader(), probe=probe, remuxer=remuxer)
    assert result.exit_code == 0, result.output
    assert remuxer.calls == [videos / "abcDEF123_-.mp4"]
    assert "Remuxing abcDEF123_-.mp4: it starts at 64.0 s" in result.output
    assert f"Remuxed: {videos / 'abcDEF123_-.mp4'}" in result.output


def test_a_download_that_starts_at_zero_is_left_alone(videos: Path) -> None:
    probe = FakeProbe(start=0.05)
    remuxer = FakeRemuxer(probe)
    result = _fetch(_URL, FakeDownloader(), probe=probe, remuxer=remuxer)
    assert result.exit_code == 0, result.output
    assert remuxer.calls == []
    assert "Remux" not in result.output


def test_a_reused_file_that_starts_late_is_remuxed_once(videos: Path) -> None:
    videos.mkdir()
    (videos / "abcDEF123_-.mp4").write_bytes(b"old download")
    probe = FakeProbe(start=64.0)
    remuxer = FakeRemuxer(probe)
    downloader = FakeDownloader()
    first = _fetch(_URL, downloader, probe=probe, remuxer=remuxer)
    second = _fetch(_URL, downloader, probe=probe, remuxer=remuxer)
    assert (first.exit_code, second.exit_code) == (0, 0)
    assert downloader.calls == []
    assert len(remuxer.calls) == 1


def test_a_failed_remux_fails_the_fetch(videos: Path) -> None:
    probe = FakeProbe(start=64.0)
    remuxer = FakeRemuxer(probe, error="ffmpeg failed (exit 1): bad")
    result = _fetch(_URL, FakeDownloader(), probe=probe, remuxer=remuxer)
    assert result.exit_code == 1
    assert "Error: ffmpeg failed (exit 1): bad" in result.output
