"""How posted Shorts do: a YouTube Studio export read, linked to drafts, and
grouped for a report.

Pure functions over the export's table text, drafts folders and the posts
record; reading files and keeping snapshots is the caller's. See
docs/decisions/0027.
"""

import csv
import difflib
import io
import re
import statistics
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from clipper.core.cuts import TimeMap
from clipper.core.posting import DraftsFolder, clip_key
from clipper.domain.errors import PerformanceError
from clipper.domain.models import ManifestClip, PostLog, PostRecord, StudioRow

# The export's table and the other files Studio puts beside it, kept
# unchanged in a snapshot (ADR 0027, first export amendment).
TABLE_FILE = "Table data.csv"
EXPORT_FILES = (TABLE_FILE, "Chart data.csv", "Totals.csv")
_TOTAL_ROW = "Total"
_REQUIRED = ("Content", "Video title", "Video publish time", "Duration")
_COUNTS = {
    "views": "Views",
    "engaged_views": "Engaged views",
    "likes": "Likes",
    "shares": "Shares",
    "comments": "Comments added",
    "subscribers_gained": "Subscribers gained",
}
_PERCENTS = {
    "stayed_to_watch": "Stayed to watch (%)",
    "average_percentage_viewed": "Average percentage viewed (%)",
}
_VIEW_DURATION = "Average view duration"
_EXPORT_NAME = re.compile(
    r"Content (\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})(?: (.+))?", re.IGNORECASE
)
_FOLDER_DAY = "%m-%d-%Y"

# A Short matches a draft whose length is within this many seconds: Studio
# gives whole seconds, and the upload may round (ADR 0027 amendment).
LENGTH_TOLERANCE_SECONDS = 2.0
# How many near titles an unlinked Short lists.
CLOSE_TITLES = 3
_CLOSE_CUTOFF = 0.6
# Length bands for the report, in seconds: (label, up to but not including).
LENGTH_BANDS = (
    ("under 30 s", 30.0),
    ("30-40 s", 40.0),
    ("40-50 s", 50.0),
    ("50 s +", float("inf")),
)


def parse_export_name(name: str) -> tuple[date, date, str | None] | None:
    """The first and last day and the channel from a Studio export folder's
    name, `Content <from>_<to> <channel>`; None for any other name."""
    match = _EXPORT_NAME.fullmatch(name.strip())
    if match is None:
        return None
    try:
        first = date.fromisoformat(match.group(1))
        last = date.fromisoformat(match.group(2))
    except ValueError:
        return None
    return first, last, match.group(3)


def parse_table(text: str) -> tuple[StudioRow, ...]:
    """The Shorts in an export's `Table data.csv`, by column name.

    `Content`, `Video title`, `Video publish time` and `Duration` are
    required; the other figures are read when their column is there, and a
    blank cell is None. The `Total` row is skipped. Raises
    `PerformanceError` naming the row and column.
    """
    reader = csv.DictReader(io.StringIO(text))
    columns = reader.fieldnames or []
    missing = [name for name in _REQUIRED if name not in columns]
    if missing:
        raise PerformanceError(f"missing column(s): {', '.join(missing)}")
    rows: dict[str, StudioRow] = {}
    for line, record in enumerate(reader, start=2):
        video_id = (record.get("Content") or "").strip()
        if not video_id or video_id == _TOTAL_ROW:
            continue
        if video_id in rows:
            raise PerformanceError(f"row {line}: {video_id} is listed twice")
        try:
            rows[video_id] = _row(record, video_id)
        except PerformanceError as exc:
            raise PerformanceError(f"row {line} ({video_id}): {exc}") from exc
    if not rows:
        raise PerformanceError("no Shorts listed")
    return tuple(rows.values())


def _row(record: dict[str, str | None], video_id: str) -> StudioRow:
    duration = _count(record, "Duration")
    if duration is None:
        raise PerformanceError("Duration is blank")
    figures: dict[str, int | float | None] = {
        field: _count(record, column) for field, column in _COUNTS.items()
    }
    figures.update(
        {field: _number(record, column) for field, column in _PERCENTS.items()}
    )
    figures["average_view_duration_seconds"] = _clock(record, _VIEW_DURATION)
    return StudioRow.model_validate(
        {
            "video_id": video_id,
            "title": (record.get("Video title") or "").strip(),
            "published": _publish_day(record.get("Video publish time") or ""),
            "duration_seconds": duration,
            **figures,
        }
    )


def _cell(record: dict[str, str | None], column: str) -> str | None:
    value = (record.get(column) or "").strip().replace(",", "")
    return value or None


def _number(record: dict[str, str | None], column: str) -> float | None:
    value = _cell(record, column)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise PerformanceError(f"{column} is not a number: {value!r}") from exc


def _count(record: dict[str, str | None], column: str) -> int | None:
    value = _number(record, column)
    return None if value is None else round(value)


def _clock(record: dict[str, str | None], column: str) -> int | None:
    """`H:MM:SS` or `M:SS` as whole seconds."""
    value = _cell(record, column)
    if value is None:
        return None
    parts = value.split(":")
    if not 2 <= len(parts) <= 3 or not all(part.isdigit() for part in parts):
        raise PerformanceError(f"{column} is not a time: {value!r}")
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def _publish_day(text: str) -> date:
    """`Nov 11, 2026` as Studio writes it, or `2026-11-11`."""
    value = " ".join(text.split())
    for parse in (
        lambda v: datetime.strptime(v, "%b %d, %Y").date(),
        date.fromisoformat,
    ):
        try:
            return parse(value)
        except ValueError:
            continue
    raise PerformanceError(f"Video publish time is not a date: {text!r}")


def fold_title(title: str) -> str:
    """A title as matched: case folded, accents, emoji and punctuation
    dropped, spaces collapsed, so a title edited at upload by an emoji or a
    full stop still matches its draft."""
    text = unicodedata.normalize("NFKD", title)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^\w\s]|_", " ", text.casefold())
    return " ".join(text.split())


@dataclass(frozen=True)
class DraftClip:
    """A rendered draft a Short may have come from: its posts key, the titles
    it went by (the maintainer's and the model's), its length, the day its
    drafts folder ran (None for an older flat folder), its style and its
    channel."""

    key: str
    titles: tuple[str, ...]
    length_seconds: float
    day: date | None
    style: str | None
    channel: str | None


def draft_clips(folders: Sequence[DraftsFolder]) -> list[DraftClip]:
    """Every clip with a file in the folders, saved or not (drafts were
    posted before `review`), and each combined clip and rendered summary."""
    drafts: list[DraftClip] = []
    for folder in folders:
        day = _folder_day(folder.location)
        manifest = folder.manifest
        for clip in manifest.clips:
            if clip.file is None:
                continue
            titles = tuple(t for t in (clip.title, clip.model_title) if t)
            drafts.append(
                DraftClip(
                    key=clip_key(folder.name, clip.file),
                    titles=titles,
                    length_seconds=_clip_length(clip),
                    day=day,
                    style=_clip_style(clip, manifest.vertical),
                    channel=folder.channel,
                )
            )
        combined = manifest.combined
        if combined is not None:
            drafts.append(
                DraftClip(
                    key=clip_key(folder.name, combined.file),
                    titles=(combined.title,) if combined.title else (),
                    length_seconds=combined.duration_seconds,
                    day=day,
                    style="combined",
                    channel=folder.channel,
                )
            )
        story = manifest.story
        if story is not None and story.file is not None:
            drafts.append(
                DraftClip(
                    key=clip_key(folder.name, story.file),
                    titles=(story.title,) if story.title else (),
                    length_seconds=story.duration_seconds or 0.0,
                    day=day,
                    style="summary",
                    channel=folder.channel,
                )
            )
    return drafts


def _folder_day(location: Path | None) -> date | None:
    """The day in `<channel>/<MM-DD-YYYY>/<stem>` (ADR 0019)."""
    if location is None or len(location.parts) < 2:
        return None
    try:
        return datetime.strptime(location.parts[-2], _FOLDER_DAY).date()
    except ValueError:
        return None


def _clip_length(clip: ManifestClip) -> float:
    if clip.sequence:
        return sum(block.duration_seconds for block in clip.sequence)
    return TimeMap(clip.start_seconds, clip.end_seconds, clip.cuts).duration


def _clip_style(clip: ManifestClip, vertical: bool) -> str | None:
    """The style of the clip's file: from its name (`<stem>.<style>.mp4`,
    ADR 0025), or None for a vertical clip with several styles rendered,
    since which was posted is not known."""
    if not vertical:
        return "landscape"
    if len(clip.variants) > 1:
        return None
    if clip.variants:
        return clip.variants[0].style
    parts = Path(clip.file or "").stem.split(".")
    return parts[-1] if len(parts) > 1 else "fit"


LinkStatus = Literal["posted", "matched", "ambiguous", "none"]


@dataclass(frozen=True)
class ShortLink:
    """A Short and the draft it came from. `posted`: the posts record has
    its video ID. `matched`: one draft fits its title, length and day.
    `ambiguous`: several fit, or titles match but length or day does not;
    `candidates` are their keys. `none`: no title matches; `close` lists
    drafts with near titles, those whose length and day fit first."""

    row: StudioRow
    status: LinkStatus
    draft: DraftClip | None = None
    candidates: tuple[str, ...] = ()
    close: tuple[str, ...] = ()


def link_shorts(
    rows: Sequence[StudioRow], drafts: Sequence[DraftClip], posts: PostLog
) -> list[ShortLink]:
    """Each Short linked to its draft (ADR 0027 decision 3): by video ID
    through the posts record, else by matching."""
    by_key = {draft.key: draft for draft in drafts}
    by_video = {post.video_id: post.clip for post in posts.posts}
    links: list[ShortLink] = []
    for row in rows:
        posted = by_video.get(row.video_id)
        if posted is not None:
            links.append(ShortLink(row, "posted", draft=by_key.get(posted)))
        else:
            links.append(_match(row, drafts))
    return links


def _match(row: StudioRow, drafts: Sequence[DraftClip]) -> ShortLink:
    wanted = fold_title(row.title)
    same = [d for d in drafts if wanted and wanted in map(fold_title, d.titles)]
    fits = [d for d in same if _fits(row, d)]
    if len(fits) == 1:
        return ShortLink(row, "matched", draft=fits[0])
    if same:
        found = fits or same
        return ShortLink(row, "ambiguous", candidates=tuple(d.key for d in found))
    fitting = [d for d in drafts if _fits(row, d)]
    near = _near(wanted, fitting) or _near(wanted, drafts)
    return ShortLink(row, "none", close=tuple(near))


def _near(wanted: str, drafts: Sequence[DraftClip]) -> list[str]:
    """Up to `CLOSE_TITLES` drafts with titles near `wanted`, nearest first,
    as `<title> (<key>)` so one can be posted by its key."""
    named = {fold_title(t): f"{t} ({d.key})" for d in drafts for t in d.titles}
    near = difflib.get_close_matches(
        wanted, list(named), n=CLOSE_TITLES, cutoff=_CLOSE_CUTOFF
    )
    return [named[n] for n in near]


def _fits(row: StudioRow, draft: DraftClip) -> bool:
    if abs(row.duration_seconds - draft.length_seconds) > LENGTH_TOLERANCE_SECONDS:
        return False
    return draft.day is None or row.published >= draft.day


def matched_posts(links: Sequence[ShortLink]) -> list[PostRecord]:
    """A post record for each Short matched to one draft, dated the day it
    was published, for `--link` to add to the posts record."""
    return [
        PostRecord(
            clip=link.draft.key,
            url=f"https://youtube.com/shorts/{link.row.video_id}",
            video_id=link.row.video_id,
            posted=link.row.published,
        )
        for link in links
        if link.status == "matched" and link.draft is not None
    ]


BAND_NAMES = tuple(label for label, _ in LENGTH_BANDS)


def length_band(seconds: float) -> str:
    """The report's length band for a Short."""
    return next(label for label, below in LENGTH_BANDS if seconds < below)


@dataclass(frozen=True)
class Group:
    """Medians of one group of Shorts; None where none reported the figure."""

    name: str
    shorts: int
    views: float | None
    stayed_to_watch: float | None
    average_percentage_viewed: float | None


def group_shorts(
    links: Sequence[ShortLink],
    name_of: Callable[[ShortLink], str | None],
    order: Sequence[str] = (),
) -> list[Group]:
    """The Shorts grouped by `name_of` (None leaves a Short out), each
    group's medians; groups in `order`, then the rest by most Shorts."""
    groups: dict[str, list[StudioRow]] = {}
    for link in links:
        name = name_of(link)
        if name is not None:
            groups.setdefault(name, []).append(link.row)
    ranked = sorted(
        groups.items(),
        key=lambda item: (
            order.index(item[0]) if item[0] in order else len(order),
            -len(item[1]),
        ),
    )
    return [
        Group(
            name=name,
            shorts=len(rows),
            views=_median([r.views for r in rows]),
            stayed_to_watch=_median([r.stayed_to_watch for r in rows]),
            average_percentage_viewed=_median(
                [r.average_percentage_viewed for r in rows]
            ),
        )
        for name, rows in ranked
    ]


def _median(values: Sequence[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    return statistics.median(known) if known else None


def by_length(link: ShortLink) -> str:
    return length_band(link.row.duration_seconds)


def by_channel(link: ShortLink) -> str | None:
    """The draft's channel, its spaces trimmed (a YouTube channel name may
    end in one)."""
    channel = link.draft.channel.strip() if link.draft and link.draft.channel else ""
    return channel or None


def by_style(link: ShortLink) -> str | None:
    return link.draft.style if link.draft else None
