from pathlib import Path

import pytest

from clipper.core.sources import (
    parse_source_list,
    parse_video_url,
    video_info_path,
    video_path,
    video_url,
)
from clipper.domain.errors import DownloadError, SourceListError
from clipper.domain.models import QueuedSource, VideoSource

# The IDs below are constructed; no real video is implied.
_YT = VideoSource(platform="youtube", video_id="abcDEF123_-")
_TW = VideoSource(platform="twitch", video_id="v1234567890")


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abcDEF123_-",
        "https://youtube.com/watch?v=abcDEF123_-",
        "https://m.youtube.com/watch?v=abcDEF123_-",
        "http://www.youtube.com/watch?v=abcDEF123_-",
        "https://www.youtube.com/watch?v=abcDEF123_-&t=90s",
        "https://www.youtube.com/watch?v=abcDEF123_-&list=PLxyz&index=3",
        "https://youtu.be/abcDEF123_-",
        "https://youtu.be/abcDEF123_-?si=share",
        "https://www.youtube.com/shorts/abcDEF123_-",
        "https://www.youtube.com/live/abcDEF123_-",
        "  https://www.YouTube.com/watch?v=abcDEF123_-  ",
    ],
)
def test_youtube_video_urls(url: str) -> None:
    assert parse_video_url(url) == _YT


@pytest.mark.parametrize(
    "url",
    [
        "https://www.twitch.tv/videos/1234567890",
        "https://twitch.tv/videos/1234567890",
        "https://m.twitch.tv/videos/1234567890?t=1h2m3s",
        "https://www.twitch.tv/videos/1234567890/",
    ],
)
def test_twitch_vod_urls(url: str) -> None:
    assert parse_video_url(url) == _TW


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("https://www.youtube.com/playlist?list=PLxyz", "Playlist URLs are not"),
        ("https://www.youtube.com/@somechannel", "Not a single YouTube video"),
        ("https://www.youtube.com/channel/UCxyz", "Not a single YouTube video"),
        ("https://www.youtube.com/", "Not a single YouTube video"),
        ("https://www.youtube.com/watch", "Not a YouTube video ID"),
        ("https://www.youtube.com/watch?v=short", "Not a YouTube video ID"),
        ("https://youtu.be/", "Not a YouTube video or Twitch VOD URL"),
        ("https://www.twitch.tv/somechannel", "Only Twitch VOD URLs"),
        ("https://www.twitch.tv/videos/abc", "Only Twitch VOD URLs"),
        ("https://www.twitch.tv/somechannel/clip/Name", "Only Twitch VOD URLs"),
        ("https://clips.twitch.tv/Name", "Only Twitch VOD URLs"),
        ("https://vimeo.com/123", "Not a YouTube video or Twitch VOD URL"),
        ("ftp://www.youtube.com/watch?v=abcDEF123_-", "Not a YouTube video or"),
        ("data/videos/abc.mp4", "Not a YouTube video or Twitch VOD URL"),
        ("", "Not a YouTube video or Twitch VOD URL"),
    ],
)
def test_other_urls_are_refused(url: str, message: str) -> None:
    with pytest.raises(DownloadError, match=message):
        parse_video_url(url)


def test_refusals_cite_the_adr() -> None:
    with pytest.raises(DownloadError, match="docs/decisions/0020"):
        parse_video_url("https://www.youtube.com/playlist?list=PLxyz")


def test_video_url_is_plain() -> None:
    assert video_url(_YT) == "https://www.youtube.com/watch?v=abcDEF123_-"
    assert video_url(_TW) == "https://www.twitch.tv/videos/1234567890"


def test_video_path_is_named_by_id() -> None:
    assert video_path(_YT, Path("data/videos")) == Path("data/videos/abcDEF123_-.mp4")
    assert video_path(_TW, Path("v")) == Path("v/v1234567890.mp4")


def test_video_info_sits_beside_the_video() -> None:
    assert video_info_path(_YT, Path("v")) == Path("v/abcDEF123_-.info.json")
    assert video_info_path(_TW, Path("v")) == Path("v/v1234567890.info.json")


def test_a_source_list_skips_comments_and_reads_about_text() -> None:
    text = (
        "# Lacy pool\n"
        "https://www.twitch.tv/videos/1 | Lacy (he/him)\n"
        "\n"
        "  data/videos/talk.mp4  \n"
        "https://youtu.be/abcDEF123_- |\n"
    )
    assert parse_source_list(text) == [
        QueuedSource(
            source="https://www.twitch.tv/videos/1", about="Lacy (he/him)", line=2
        ),
        QueuedSource(source="data/videos/talk.mp4", line=4),
        QueuedSource(source="https://youtu.be/abcDEF123_-", line=5),
    ]


def test_an_empty_list_has_no_sources() -> None:
    assert parse_source_list("# nothing yet\n\n") == []


def test_about_text_without_a_source_is_refused() -> None:
    with pytest.raises(SourceListError, match="Line 2: no source before the about"):
        parse_source_list("a.mp4\n | Lacy\n")
