from pathlib import Path

from clipper.domain.errors import DownloadError
from clipper.domain.models import VideoInfo


def save_video_info(info: VideoInfo, path: Path) -> None:
    """Write a fetched video's metadata as JSON. See docs/decisions/0020.

    Overwrites `path`; the caller decides whether that is allowed.
    """
    text = info.model_dump_json(indent=2)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8", newline="\n")
    except OSError as exc:
        raise DownloadError(f"Cannot write video info {path}: {exc}") from exc
