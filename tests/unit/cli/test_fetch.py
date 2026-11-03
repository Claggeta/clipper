from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from clipper.adapters.download.ytdlp import YtDlpDownloader
from clipper.cli.context import AppContext
from clipper.cli.main import app
from clipper.core.config import Settings
from clipper.domain.errors import DownloadError
from clipper.domain.models import VideoSource

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


@pytest.fixture
def videos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "videos"
    monkeypatch.setenv("CLIPPER_VIDEOS_DIR", str(folder))
    return folder


def _fetch(url: str, downloader: FakeDownloader) -> Result:
    context = AppContext(downloader=lambda settings: downloader)
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
