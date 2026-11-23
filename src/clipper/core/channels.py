"""Saved about text and cam boxes per channel, and the people the review
page offers.

Pure functions: parsing the channels and cams files and finding a video's
line, writing a cams line, and parsing the people file. Reading and writing
the files is the caller's. See docs/decisions/0018 (saved about amendment),
0024 (rating amendment) and 0025 (12b amendment).
"""

from collections.abc import Mapping

from pydantic import ValidationError

from clipper.core.sources import ABOUT_SEPARATOR
from clipper.domain.errors import FaceError, ReviewError, TitleError
from clipper.domain.models import FacecamBox, VideoInfo

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


_CAMS_HINT = "write it as <channel> | <x> <y> <w> <h>, fractions of the frame"


def parse_channel_cams(text: str) -> dict[str, FacecamBox]:
    """`<channel> | <x> <y> <w> <h>` per line, keyed by channel ignoring
    case; the box as fractions of the frame, `x` and `y` its top-left.

    Blank lines and `#` comments are skipped. Raises `FaceError` for a line
    without four numbers, a box outside the frame, or a channel given twice.
    """
    cams: dict[str, FacecamBox] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        channel, _, numbers = line.partition(ABOUT_SEPARATOR)
        channel = channel.strip()
        try:
            x, y, w, h = (float(v) for v in numbers.split())
            box = FacecamBox(x=x, y=y, w=w, h=h)
        except (ValueError, ValidationError) as exc:
            raise FaceError(f"Line {number}: {_CAMS_HINT}") from exc
        if not channel:
            raise FaceError(f"Line {number}: {_CAMS_HINT}")
        key = channel.casefold()
        if key in cams:
            raise FaceError(f"Line {number}: {channel!r} is given twice")
        cams[key] = box
    return cams


def channel_cam(
    cams: Mapping[str, FacecamBox], channel: str | None
) -> FacecamBox | None:
    """The saved cam box for the channel, compared as `channel_about` does."""
    if channel is None:
        return None
    return cams.get(channel.strip().casefold())


def set_channel_cam(text: str, channel: str, box: FacecamBox | None) -> str:
    """The cams file's text with the channel's line replaced, added at the
    end, or removed for no box; other lines and comments stay as they are."""
    key = channel.strip().casefold()
    lines = [
        raw
        for raw in text.splitlines()
        if raw.strip().startswith("#")
        or raw.partition(ABOUT_SEPARATOR)[0].strip().casefold() != key
        or not raw.strip()
    ]
    if box is not None:
        # Rounded, and kept inside the frame after rounding.
        x, y = round(box.x, 4), round(box.y, 4)
        w, h = min(round(box.w, 4), 1 - x), min(round(box.h, 4), 1 - y)
        lines.append(
            f"{channel.strip()} {ABOUT_SEPARATOR} {x:.4f} {y:.4f} {w:.4f} {h:.4f}"
        )
    return "".join(f"{line}\n" for line in lines)


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
