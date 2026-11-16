import logging
import os
import shutil
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from clipper.adapters.results.labels_json import load_labels
from clipper.adapters.results.manifest_json import find_drafts_folders, load_manifest
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


def write_uploads(folder: Path, files: Sequence[tuple[Path, str]]) -> None:
    """Fill the upload folder: each source under its new name, as a hard
    link (a copy where one cannot be made), after removing the `.mp4` and
    `.jpg` files a previous queue left there (ADR 0028, upload names).

    A missing source is skipped with a warning. Raises `QueueError` naming
    the file that cannot be removed or written.
    """
    try:
        folder.mkdir(parents=True, exist_ok=True)
        old = [p for p in folder.iterdir() if p.suffix.lower() in (".mp4", ".jpg")]
    except OSError as exc:
        raise QueueError(f"Cannot open upload folder {folder}: {exc}") from exc
    for path in old:
        try:
            path.unlink()
        except OSError as exc:
            raise QueueError(f"Cannot remove {path}: {exc}") from exc
    for source, name in files:
        target = folder / name
        if not source.is_file():
            logger.warning("No file to upload at %s", source)
            continue
        try:
            os.link(source, target)
        except OSError:
            try:
                shutil.copy2(source, target)
            except OSError as exc:
                raise QueueError(f"Cannot write {target}: {exc}") from exc


def load_drafts_folders(
    clips_dir: Path, labels_dir: Path, videos_dir: Path
) -> list[DraftsFolder]:
    """Every drafts folder under `clips_dir`, at any depth, newest manifest
    first, with its judged labels and its video's channel.

    A folder whose manifest or labels cannot be read is skipped with a
    warning, and a bad info file only loses the channel: an old or broken
    folder must not stop the queue.
    """
    folders: list[DraftsFolder] = []
    for path in find_drafts_folders(clips_dir):
        try:
            manifest = load_manifest(path)
            labels_path = judged_labels_path(labels_dir, manifest.source)
            labels = load_labels(labels_path) if labels_path.is_file() else None
        except ClipperError as exc:
            logger.warning("Queue skips %s: %s", path, exc)
            continue
        info_path = videos_dir / f"{Path(manifest.source).stem}.info.json"
        try:
            info = load_video_info(info_path)
        except ClipperError as exc:
            logger.warning("Queue has no channel for %s: %s", path, exc)
            info = None
        folders.append(
            DraftsFolder(
                name=path.name,
                manifest=manifest,
                labels=labels,
                channel=info.channel if info else None,
                location=path.relative_to(clips_dir),
            )
        )
    return folders
