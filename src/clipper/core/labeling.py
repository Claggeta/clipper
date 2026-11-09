"""Labels from Twitch community clips. See docs/decisions/0012.

A links file lists one clip per line: the clip's URL and its "Watch full
video" URL, a Twitch VOD URL with `?t=`. `t` marks the clip's end in the VOD,
so a clip spans `t - length` to `t`, and overlapping clips merge into one
label (ADR 0012, Twitch clips amendment).
"""

import re
from collections.abc import Sequence
from urllib.parse import parse_qs, urlsplit

from clipper.core.sources import parse_video_url
from clipper.domain.errors import DownloadError, LabelsError
from clipper.domain.models import (
    ClipInfo,
    ClipLink,
    Label,
    LabeledVideo,
    format_timestamp,
)

_CLIP_HOSTS = frozenset({"twitch.tv", "www.twitch.tv", "m.twitch.tv"})
_CLIPS_HOST = "clips.twitch.tv"
_CLIP_ID = re.compile(r"[A-Za-z0-9_-]+")
_OFFSET = re.compile(r"(?:([0-9]+)h)?(?:([0-9]+)m)?(?:([0-9]+)s)?")


def parse_clip_url(url: str) -> str:
    """Return the clip ID (slug) a Twitch clip URL names, or raise `LabelsError`.

    Accepts `twitch.tv/<channel>/clip/<id>` and `clips.twitch.tv/<id>`. The
    query, such as `?range=7d`, is ignored.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    segments = [s for s in parts.path.split("/") if s]
    clip_id = ""
    if parts.scheme in ("http", "https"):
        if host == _CLIPS_HOST and len(segments) == 1:
            clip_id = segments[0]
        elif host in _CLIP_HOSTS and len(segments) == 3 and segments[1] == "clip":
            clip_id = segments[2]
    if not _CLIP_ID.fullmatch(clip_id):
        raise LabelsError(f"Not a Twitch clip URL: {url}")
    return clip_id


def clip_url(clip_id: str) -> str:
    """The plain URL of one clip, with no query."""
    return f"https://{_CLIPS_HOST}/{clip_id}"


def parse_twitch_offset(value: str) -> float:
    """Convert a Twitch `t` value such as `4h36m45s`, `36m5s` or `45s` to seconds.

    Raises `LabelsError` for anything else, including a bare number.
    """
    match = _OFFSET.fullmatch(value)
    if not value or match is None:
        raise LabelsError(f"t={value!r} is not a time like 4h36m45s")
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def parse_links(text: str, name: str) -> list[ClipLink]:
    """Parse a links file's text. `name` (the file) prefixes every error.

    One clip per line: `<clip url> <vod url with ?t=>`. Blank lines and `#`
    comments, whole-line or after the two URLs, are skipped. Every line must
    name the same Twitch VOD, a clip may appear once, and there must be at
    least one clip. Raises `LabelsError` naming the line.
    """
    links: list[ClipLink] = []
    lines_by_clip: dict[str, int] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        fields: list[str] = []
        for field in line.split():
            if field.startswith("#"):
                break
            fields.append(field)
        if not fields:
            continue
        try:
            link = _parse_line(number, fields)
        except LabelsError as exc:
            raise LabelsError(f"{name} line {number}: {exc}") from exc
        if link.clip_id in lines_by_clip:
            raise LabelsError(
                f"{name} line {number}: clip {link.clip_id} is already on line "
                f"{lines_by_clip[link.clip_id]}"
            )
        if links and link.vod != links[0].vod:
            raise LabelsError(
                f"{name} line {number}: VOD {link.vod.video_id} differs from "
                f"{links[0].vod.video_id} on line {links[0].line}; a links file "
                "covers one VOD"
            )
        lines_by_clip[link.clip_id] = number
        links.append(link)
    if not links:
        raise LabelsError(f"{name} lists no clips")
    return links


def _parse_line(number: int, fields: list[str]) -> ClipLink:
    if len(fields) != 2:
        raise LabelsError(
            f"expected a clip URL and a VOD URL, got {len(fields)} field(s)"
        )
    clip, vod = fields
    try:
        source = parse_video_url(vod)
    except DownloadError as exc:
        raise LabelsError(str(exc)) from exc
    if source.platform != "twitch":
        raise LabelsError(f"Not a Twitch VOD URL: {vod}")
    offsets = parse_qs(urlsplit(vod).query).get("t")
    if not offsets:
        raise LabelsError(f"the VOD URL has no t= (use 'Watch full video'): {vod}")
    end = parse_twitch_offset(offsets[0])
    if end <= 0:
        raise LabelsError(f"t={offsets[0]!r} is at the very start of the VOD")
    return ClipLink(
        line=number, clip_id=parse_clip_url(clip), vod=source, end_seconds=end
    )


def clip_labels(
    source: str, links: Sequence[ClipLink], infos: Sequence[ClipInfo]
) -> LabeledVideo:
    """Turn clips into labels: `t - length` to `t`, overlapping spans merged.

    `infos[i]` belongs to `links[i]`. A start before 0 is clamped to 0.
    Touching spans stay separate. A label's note lists its clips' titles in
    time order.
    """
    if len(links) != len(infos):
        raise ValueError("links and infos must have the same length")
    spans = sorted(
        (
            (max(0.0, link.end_seconds - info.duration_seconds), link.end_seconds, info)
            for link, info in zip(links, infos, strict=True)
        ),
        key=lambda span: (span[0], span[1]),
    )
    groups: list[tuple[float, float, list[str]]] = []
    for start, end, info in spans:
        if groups and start < groups[-1][1]:
            first, last, titles = groups[-1]
            groups[-1] = (first, max(last, end), [*titles, info.title])
        else:
            groups.append((start, end, [info.title]))
    labels = tuple(
        Label.model_validate(
            {
                "start": format_timestamp(start),
                "end": format_timestamp(end),
                "note": "Twitch clips: " + "; ".join(titles),
            }
        )
        for start, end, titles in groups
    )
    return LabeledVideo(source=source, labels=labels)
