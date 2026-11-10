from pathlib import Path

import pytest

from clipper.adapters.results.video_info_json import (
    load_video_info,
    save_video_info,
    video_info_beside,
)
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


def test_info_sits_beside_any_video() -> None:
    assert video_info_beside(Path("x/talk.mp4")) == Path("x/talk.info.json")


def test_loading_reads_saved_info(tmp_path: Path) -> None:
    path = tmp_path / "v1234567890.info.json"
    save_video_info(_INFO, path)
    assert load_video_info(path) == _INFO


def test_no_file_loads_as_none(tmp_path: Path) -> None:
    assert load_video_info(tmp_path / "missing.info.json") is None


def test_a_bad_file_is_a_download_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.info.json"
    path.write_text('{"title": "x"}', encoding="utf-8")
    with pytest.raises(DownloadError, match="Cannot read video info"):
        load_video_info(path)
