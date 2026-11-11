"""Saved about text per channel, for titles when `--about` is not given.

Pure functions: parsing the channels file and finding a video's line.
Reading the file is the caller's. See docs/decisions/0018 (saved about
amendment).
"""

from collections.abc import Mapping

from clipper.core.sources import ABOUT_SEPARATOR
from clipper.domain.errors import TitleError
from clipper.domain.models import VideoInfo

# The longest about text, saved or given with --about (ADR 0018, 8c).
ABOUT_LIMIT = 300


def parse_channel_abouts(text: str) -> dict[str, str]:
    """`<channel> | <about text>` per line, keyed by channel ignoring case.

    Blank lines and lines starting with `#` are skipped. Raises `TitleError`
    for a line without a channel or about text, an about text over
    `ABOUT_LIMIT`, or a channel given twice.
    """
    abouts: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        channel, _, about = line.partition(ABOUT_SEPARATOR)
        channel, about = channel.strip(), about.strip()
        if not channel or not about:
            raise TitleError(
                f"Line {number}: write it as <channel> {ABOUT_SEPARATOR} <about text>"
            )
        if len(about) > ABOUT_LIMIT:
            raise TitleError(
                f"Line {number}: the about text is {len(about)} characters; "
                f"keep it to {ABOUT_LIMIT}"
            )
        key = channel.casefold()
        if key in abouts:
            raise TitleError(f"Line {number}: {channel!r} is given twice")
        abouts[key] = about
    return abouts


def channel_about(abouts: Mapping[str, str], video: VideoInfo | None) -> str | None:
    """The saved about text for the video's channel, or None.

    Channels compare ignoring case and surrounding spaces: yt-dlp gave one
    YouTube channel as "Lacy " with a trailing space.
    """
    if video is None:
        return None
    return abouts.get(video.channel.strip().casefold())
