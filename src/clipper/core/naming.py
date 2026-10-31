import math
from collections.abc import Sequence
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
