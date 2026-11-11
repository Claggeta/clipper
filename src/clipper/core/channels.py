"""Saved about text per channel, and the people the review page offers.

Pure functions: parsing the channels file and finding a video's line, and
parsing the people file. Reading the files is the caller's. See
docs/decisions/0018 (saved about amendment) and 0024 (rating amendment).
"""

from collections.abc import Mapping

from clipper.core.sources import ABOUT_SEPARATOR
from clipper.domain.errors import ReviewError, TitleError
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


# The longest name in the people file.
PERSON_LIMIT = 40


def parse_people(text: str) -> tuple[str, ...]:
    """One name per line, in order; blank lines and `#` comments skipped.

    Raises `ReviewError` for a name over `PERSON_LIMIT` characters or given
    twice, ignoring case.
    """
    names: list[str] = []
    seen: set[str] = set()
    for number, raw in enumerate(text.splitlines(), start=1):
        name = raw.strip()
        if not name or name.startswith("#"):
            continue
        if len(name) > PERSON_LIMIT:
            raise ReviewError(
                f"Line {number}: a name is at most {PERSON_LIMIT} characters"
            )
        if name.casefold() in seen:
            raise ReviewError(f"Line {number}: {name!r} is given twice")
        seen.add(name.casefold())
        names.append(name)
    return tuple(names)
