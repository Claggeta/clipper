import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from clipper.domain.errors import DownloadError
from clipper.domain.models import VideoSource

# Hosts `fetch` reads single videos from (ADR 0020). Anything else is refused.
_YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com"})
_SHORT_HOSTS = frozenset({"youtu.be", "www.youtu.be"})
_TWITCH_HOSTS = frozenset(
    {"twitch.tv", "www.twitch.tv", "m.twitch.tv", "clips.twitch.tv"}
)
_YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_TWITCH_ID = re.compile(r"[0-9]+")
_ADR = "docs/decisions/0020"


def parse_video_url(url: str) -> VideoSource:
    """Return the one video a URL names, or raise `DownloadError`.

    Accepts YouTube `watch?v=`, `youtu.be/`, `shorts/` and `live/` URLs, and
    Twitch `videos/<number>` URLs. Query parameters other than `v` are ignored,
    so a video URL that carries a playlist still names one video. Playlist,
    channel, Twitch live and clip URLs are refused, not expanded (ADR 0020).
    """
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    segments = [s for s in parts.path.split("/") if s]
    if parts.scheme in ("http", "https"):
        if host in _SHORT_HOSTS and len(segments) == 1:
            return _youtube(segments[0], url)
        if host in _YOUTUBE_HOSTS:
            if segments == ["watch"]:
                return _youtube(parse_qs(parts.query).get("v", [""])[0], url)
            if len(segments) == 2 and segments[0] in ("shorts", "live"):
                return _youtube(segments[1], url)
            if segments == ["playlist"]:
                raise DownloadError(
                    f"Playlist URLs are not fetched ({_ADR}); pass one video's "
                    f"URL: {url}"
                )
            raise DownloadError(
                "Not a single YouTube video URL; channels and playlists are not "
                f"fetched ({_ADR}): {url}"
            )
        if host in _TWITCH_HOSTS:
            if (
                host != "clips.twitch.tv"
                and len(segments) == 2
                and segments[0] == "videos"
                and _TWITCH_ID.fullmatch(segments[1])
            ):
                return VideoSource(platform="twitch", video_id=f"v{segments[1]}")
            raise DownloadError(
                "Only Twitch VOD URLs, twitch.tv/videos/<number>, are fetched "
                f"({_ADR}): {url}"
            )
    raise DownloadError(f"Not a YouTube video or Twitch VOD URL: {url}")


def video_url(source: VideoSource) -> str:
    """The plain URL of the one video, with no playlist or time parameters."""
    if source.platform == "youtube":
        return f"https://www.youtube.com/watch?v={source.video_id}"
    return f"https://www.twitch.tv/videos/{source.video_id.removeprefix('v')}"


def video_path(source: VideoSource, folder: Path) -> Path:
    """Where a fetched video is saved: `<folder>/<video id>.mp4`."""
    return folder / f"{source.video_id}.mp4"


def _youtube(candidate: str, url: str) -> VideoSource:
    if not _YOUTUBE_ID.fullmatch(candidate):
        raise DownloadError(f"Not a YouTube video ID in URL: {url}")
    return VideoSource(platform="youtube", video_id=candidate)
