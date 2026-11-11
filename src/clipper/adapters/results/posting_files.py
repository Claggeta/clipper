import logging
from pathlib import Path

from pydantic import ValidationError

from clipper.adapters.results.labels_json import load_labels
from clipper.adapters.results.manifest_json import MANIFEST_NAME, load_manifest
from clipper.adapters.results.video_info_json import load_video_info
from clipper.core.judging import judged_labels_path
from clipper.core.posting import DraftsFolder, parse_campaigns
from clipper.domain.errors import ClipperError, QueueError
from clipper.domain.models import Campaign, PostLog

logger = logging.getLogger(__name__)


def load_campaigns(path: Path) -> tuple[Campaign, ...]:
    """Read the campaigns file; no file means no campaigns.

    See docs/decisions/0028. Raises `QueueError` naming the file.
    """
    if not path.is_file():
        return ()
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise QueueError(f"Cannot read campaigns file {path}: {exc}") from exc
    try:
        return parse_campaigns(text)
    except QueueError as exc:
        raise QueueError(f"{path}: {exc}") from exc


def load_posts(path: Path) -> PostLog:
    """Read the posts record; no file means nothing posted yet."""
    if not path.is_file():
        return PostLog()
    try:
        return PostLog.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise QueueError(f"Cannot read posts file {path}: {exc}") from exc


def save_posts(posts: PostLog, path: Path) -> None:
    """Write the posts record as JSON, UTF-8 with LF line endings."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(posts.model_dump_json(indent=2).encode("utf-8") + b"\n")
    except OSError as exc:
        raise QueueError(f"Cannot write posts file {path}: {exc}") from exc


def load_drafts_folders(
    clips_dir: Path, labels_dir: Path, videos_dir: Path
) -> list[DraftsFolder]:
    """Every drafts folder under `clips_dir`, newest manifest first, with its
    judged labels and its video's channel.

    A folder whose manifest or labels cannot be read is skipped with a
    warning, and a bad info file only loses the channel: an old or broken
    folder must not stop the queue.
    """
    paths = sorted(
        clips_dir.glob(f"*/{MANIFEST_NAME}"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    folders: list[DraftsFolder] = []
    for path in paths:
        try:
            manifest = load_manifest(path.parent)
            labels_path = judged_labels_path(labels_dir, manifest.source)
            labels = load_labels(labels_path) if labels_path.is_file() else None
        except ClipperError as exc:
            logger.warning("Queue skips %s: %s", path.parent, exc)
            continue
        info_path = videos_dir / f"{Path(manifest.source).stem}.info.json"
        try:
            info = load_video_info(info_path)
        except ClipperError as exc:
            logger.warning("Queue has no channel for %s: %s", path.parent, exc)
            info = None
        folders.append(
            DraftsFolder(
                name=path.parent.name,
                manifest=manifest,
                labels=labels,
                channel=info.channel if info else None,
            )
        )
    return folders
