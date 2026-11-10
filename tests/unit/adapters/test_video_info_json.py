from pathlib import Path

import pytest

from clipper.adapters.results.video_info_json import save_video_info
from clipper.domain.errors import DownloadError
from clipper.domain.models import VideoInfo

# Constructed metadata.
_INFO = VideoInfo(
    platform="twitch",
    video_id="v1234567890",
    title="WE HERE ✨",
    channel="streamer",
    upload_date="20261102",
    duration_seconds=18463,
)


def test_saved_info_reads_back_the_same(tmp_path: Path) -> None:
    path = tmp_path / "videos" / "v1234567890.info.json"

    save_video_info(_INFO, path)

    assert VideoInfo.model_validate_json(path.read_bytes()) == _INFO
    assert "WE HERE ✨" in path.read_text(encoding="utf-8")


def test_a_write_failure_is_a_download_error(tmp_path: Path) -> None:
    folder = tmp_path / "taken"
    folder.mkdir()
    with pytest.raises(DownloadError, match="Cannot write video info"):
        save_video_info(_INFO, folder)
