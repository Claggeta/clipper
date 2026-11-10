from pathlib import Path

from pydantic import ValidationError

from clipper.domain.errors import DownloadError
from clipper.domain.models import VideoInfo


def video_info_beside(video: Path) -> Path:
    """The metadata file beside a video: `<stem>.info.json` (ADR 0020)."""
    return video.with_name(f"{video.stem}.info.json")


def load_video_info(path: Path) -> VideoInfo | None:
    """Read a video's saved metadata, or None if there is no file.

    Raises `DownloadError` if the file cannot be read or is not valid.
    """
    if not path.is_file():
        return None
    try:
        return VideoInfo.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise DownloadError(f"Cannot read video info {path}: {exc}") from exc


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
