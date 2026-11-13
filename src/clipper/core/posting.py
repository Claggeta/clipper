"""The post queue: which saved clips to post next, when, and for which campaigns.

Pure functions over manifests, judged labels and the posts record; reading
and writing them is the caller's. See docs/decisions/0028.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from clipper.core.judging import is_own_label
from clipper.core.sources import ABOUT_SEPARATOR
from clipper.domain.errors import QueueError
from clipper.domain.models import (
    Campaign,
    Label,
    LabeledVideo,
    PostLog,
    PostRecord,
    RenderManifest,
)

# Every caption ends with this, after the campaigns' hashtags.
SHORTS_TAG = "#Shorts"


def parse_campaigns(text: str) -> tuple[Campaign, ...]:
    """`<name> | <hashtag> | <minimum> | <people> [| <channels>]` per line.

    People and channels are comma-separated. Blank lines and lines starting
    with `#` are skipped. Raises `QueueError` naming the line.
    """
    campaigns: list[Campaign] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = [field.strip() for field in line.split(ABOUT_SEPARATOR)]
        if len(fields) not in (4, 5):
            raise QueueError(
                f"Line {number}: write it as <name> | <hashtag> | <minimum> | "
                "<people> [| <channels>]"
            )
        name, hashtag, minimum, people = fields[:4]
        channels = fields[4] if len(fields) == 5 else ""
        if not minimum.isdigit() or int(minimum) < 1:
            raise QueueError(f"Line {number}: the minimum must be a whole number")
        if not hashtag.startswith("#") or " " in hashtag or len(hashtag) < 2:
            raise QueueError(f"Line {number}: the hashtag must be one #word")
        names = _names(people)
        if not names:
            raise QueueError(f"Line {number}: name at least one person")
        if int(minimum) > len(names):
            raise QueueError(f"Line {number}: the minimum is more than the people")
        campaigns.append(
            Campaign(
                name=name,
                hashtag=hashtag,
                minimum=int(minimum),
                people=names,
                channels=_names(channels),
            )
        )
    return tuple(campaigns)


def _names(text: str) -> tuple[str, ...]:
    return tuple(name.strip() for name in text.split(",") if name.strip())


def _fold(names: Sequence[str]) -> set[str]:
    return {name.casefold() for name in names}


@dataclass(frozen=True)
class Eligibility:
    """The campaigns a clip qualifies for, and those it would but for its
    channel (a brief that takes only the person's own accounts)."""

    campaigns: tuple[Campaign, ...]
    wrong_channel: tuple[Campaign, ...]


def eligibility(
    campaigns: Sequence[Campaign], people: Sequence[str], channel: str | None
) -> Eligibility:
    """Which campaigns a clip with `people` on screen, from `channel`, fits."""
    on_screen = _fold(people)
    fits: list[Campaign] = []
    wrong: list[Campaign] = []
    for campaign in campaigns:
        if len(on_screen & _fold(campaign.people)) < campaign.minimum:
            continue
        if campaign.channels and (
            channel is None
            or channel.strip().casefold() not in _fold(campaign.channels)
        ):
            wrong.append(campaign)
        else:
            fits.append(campaign)
    return Eligibility(tuple(fits), tuple(wrong))


@dataclass(frozen=True)
class DraftsFolder:
    """One drafts folder as the queue reads it: its name (the video's stem),
    its manifest, its judged labels (None if none yet), its channel, and
    where it sits under the clips dir (`<channel>/<date>/<stem>`, or just
    the name for an older flat folder; ADR 0019)."""

    name: str
    manifest: RenderManifest
    labels: LabeledVideo | None
    channel: str | None
    location: Path | None = None


@dataclass(frozen=True)
class QueueItem:
    """A saved clip waiting to be posted."""

    clip: str
    folder: str
    path: Path
    rank: int
    title: str | None
    hook: str | None
    rating: str
    people: tuple[str, ...]
    eligible: Eligibility
    # Its thumbnail beside it, under the clips dir, if one was made (ADR 0030).
    thumbnail: Path | None = None
    # The folder's summary rather than one clip (ADR 0032); its rank is 0.
    summary: bool = False

    @property
    def place(self) -> str:
        """`#<rank>`, or `summary`, after the video's name."""
        return "summary" if self.summary else f"#{self.rank}"


def clip_key(folder: str, file: str) -> str:
    """How the posts record names a clip: `<folder>/<file>`."""
    return f"{folder}/{file}"


def build_queue(
    folders: Sequence[DraftsFolder],
    campaigns: Sequence[Campaign],
    posts: PostLog,
    include_ok: bool = False,
) -> list[QueueItem]:
    """Saved clips with a file and a rating of good (or ok, if included) that
    are not posted yet, one folder after another in turn.

    `folders` come newest first; the queue takes each folder's next clip in
    rank order, round and round, so two clips of one video are never next to
    each other while another video has clips left. Good clips come before ok
    ones. A folder's summary, rated the same, comes after its clips (ADR
    0032).
    """
    posted = {post.clip for post in posts.posts}
    ratings = ("good", "ok") if include_ok else ("good",)
    queue: list[QueueItem] = []
    for rating in ratings:
        lists = [_waiting(folder, campaigns, posted, rating) for folder in folders]
        while any(lists):
            for waiting in lists:
                if waiting:
                    queue.append(waiting.pop(0))
    return queue


def _waiting(
    folder: DraftsFolder,
    campaigns: Sequence[Campaign],
    posted: set[str],
    rating: str,
) -> list[QueueItem]:
    items: list[QueueItem] = []
    labels = folder.labels.labels if folder.labels is not None else ()
    for clip in sorted(folder.manifest.clips, key=lambda c: c.rank):
        if clip.reviewed != "saved" or clip.file is None:
            continue
        key = clip_key(folder.name, clip.file)
        if key in posted:
            continue
        label = next((x for x in labels if is_own_label(x, clip)), None)
        if label is None or label.rating != rating:
            continue
        place = folder.location or Path(folder.name)
        items.append(
            QueueItem(
                clip=key,
                folder=folder.name,
                path=place / clip.file,
                rank=clip.rank,
                title=clip.title,
                hook=clip.hook,
                rating=rating,
                people=label.people,
                eligible=eligibility(campaigns, label.people, folder.channel),
                thumbnail=place / clip.thumbnail if clip.thumbnail else None,
            )
        )
    summary = folder.manifest.summary
    if summary is not None and summary.rating == rating:
        key = clip_key(folder.name, summary.file)
        if key not in posted:
            items.append(_summary_item(folder, campaigns, labels, key, rating))
    return items


def _summary_item(
    folder: DraftsFolder,
    campaigns: Sequence[Campaign],
    labels: Sequence[Label],
    key: str,
    rating: str,
) -> QueueItem:
    """The folder's summary as a queue item: on screen, everyone marked on
    its clips, in order, so its campaigns follow theirs (ADR 0032)."""
    summary = folder.manifest.summary
    assert summary is not None
    clips = {clip.rank: clip for clip in folder.manifest.clips}
    people: list[str] = []
    for part in summary.parts:
        clip = clips.get(part.rank)
        label = (
            None
            if clip is None
            else next((x for x in labels if is_own_label(x, clip)), None)
        )
        for name in label.people if label is not None else ():
            if name not in people:
                people.append(name)
    place = folder.location or Path(folder.name)
    return QueueItem(
        clip=key,
        folder=folder.name,
        path=place / summary.file,
        rank=0,
        title=summary.title,
        hook=None,
        rating=rating,
        people=tuple(people),
        eligible=eligibility(campaigns, people, folder.channel),
        thumbnail=place / summary.thumbnail if summary.thumbnail else None,
        summary=True,
    )


def schedule(
    queue: Sequence[QueueItem], start: date, per_day: int
) -> list[tuple[date, QueueItem]]:
    """A posting day for each clip, `per_day` a day from `start`."""
    if per_day < 1:
        raise QueueError("Post at least one clip a day")
    return [
        (start + timedelta(days=index // per_day), item)
        for index, item in enumerate(queue)
    ]


def caption(item: QueueItem) -> str:
    """The post's caption: the hook (or the title), who is on screen, and the
    hashtags of every campaign the clip fits, then `SHORTS_TAG`."""
    parts = [item.hook or item.title or ""]
    if item.people:
        parts.append(f"With {_and(item.people)}.")
    tags = [c.hashtag for c in item.eligible.campaigns]
    parts.append(" ".join(dict.fromkeys([*tags, SHORTS_TAG])))
    return " ".join(part for part in parts if part)


def _and(names: Sequence[str]) -> str:
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def record_post(posts: PostLog, record: PostRecord) -> tuple[PostLog, bool]:
    """The record with `record` added, replacing an earlier one for the same
    clip; and whether it replaced one."""
    kept = tuple(post for post in posts.posts if post.clip != record.clip)
    replaced = len(kept) != len(posts.posts)
    return PostLog(posts=(*kept, record)), replaced


def queue_markdown(scheduled: Sequence[tuple[date, QueueItem]], clips_dir: Path) -> str:
    """The queue as a page to post from: per clip its day, file, title and
    caption in code blocks for copying, and its campaigns."""
    lines = ["# Post queue", ""]
    if not scheduled:
        lines.append("Nothing to post: no saved clip rated good is left.")
    for number, (day, item) in enumerate(scheduled, start=1):
        lines += [
            f"## {number}. {day.isoformat()}: {item.folder} {item.place}",
            "",
            f"`{clips_dir / item.path}`",
            "",
            "```text",
            item.title or "(no title)",
            "```",
            "",
            "```text",
            caption(item),
            "```",
            "",
            f"- Campaigns: {_campaign_list(item)}",
            f"- On screen: {', '.join(item.people) or 'not marked'}",
            *(
                [f"- Thumbnail: `{clips_dir / item.thumbnail}`"]
                if item.thumbnail
                else []
            ),
            f"- Rating: {item.rating}",
            "",
        ]
    return "\n".join(lines)


def _campaign_list(item: QueueItem) -> str:
    names = [c.name for c in item.eligible.campaigns]
    names += [f"{c.name} (not from its channels)" for c in item.eligible.wrong_channel]
    return ", ".join(names) or "none"
