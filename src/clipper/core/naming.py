import math
import re
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from clipper.domain.errors import ClipRenderError
from clipper.domain.models import ClipCandidate


def _hhmmss(seconds: int) -> str:
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}{minutes:02d}{secs:02d}"


def clip_file_name(video: Path, candidate: ClipCandidate) -> str:
    """Name a clip `<stem>_<HHMMSS>-<HHMMSS>.mp4`. See docs/decisions/0008.

    Start is floored and end is ceiled to whole seconds. Times are rounded to
    milliseconds first so float noise cannot move a name by a second. Hours
    past 99 widen the field rather than truncate.
    """
    start = math.floor(round(candidate.start_seconds, 3))
    end = math.ceil(round(candidate.end_seconds, 3))
    return f"{video.stem}_{_hhmmss(start)}-{_hhmmss(end)}.mp4"


def variant_file_name(name: str, style: str) -> str:
    """A style's file name: the clip's own for `fit`, `<stem>.<style>.mp4`
    otherwise, so the files sit side by side (ADR 0025)."""
    if style == "fit":
        return name
    path = Path(name)
    return f"{path.stem}.{style}{path.suffix}"


LOCAL_CHANNEL = "Local"
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def channel_folder_name(channel: str | None) -> str:
    """A channel's name as a folder name: characters Windows refuses dropped,
    trailing dots and spaces trimmed, `Local` when there is none."""
    name = _UNSAFE.sub("", channel or "").strip().rstrip(". ")
    return name or LOCAL_CHANNEL


def clip_folder(video: Path, clips_dir: Path, channel: str | None, day: date) -> Path:
    """The folder for one video's clips,
    `<clips dir>/<channel>/<MM-DD-YYYY>/<stem>`. See ADR 0019.

    `day` is the day the drafts ran; the caller reuses an existing folder for
    the same video instead, so a re-run does not start a second one.
    """
    return clips_dir / channel_folder_name(channel) / f"{day:%m-%d-%Y}" / video.stem


def plan_clip_paths(
    video: Path, candidates: Sequence[ClipCandidate], clips_dir: Path
) -> list[Path]:
    """Return one output path per candidate, in order.

    Raises ClipRenderError if two candidates get the same name, so a run fails
    before anything is cut instead of silently overwriting its own output.
    """
    paths: list[Path] = []
    owners: dict[Path, ClipCandidate] = {}
    for candidate in candidates:
        path = clips_dir / clip_file_name(video, candidate)
        if path in owners:
            first = owners[path]
            raise ClipRenderError(
                f"Candidates {first.start_seconds}-{first.end_seconds}s and "
                f"{candidate.start_seconds}-{candidate.end_seconds}s "
                f"both map to {path.name}"
            )
        owners[path] = candidate
        paths.append(path)
    return paths
