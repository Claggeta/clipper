"""What the review page has judged so far, and how a judgment is written.

The server holds one `ReviewStore` for the run. Each Save or Trash reads and
replaces the labels and manifest under one lock, since the server answers
requests on several threads. See docs/decisions/0024 (item 3c amendment).
"""

import math
import threading
from dataclasses import dataclass
from pathlib import Path

from clipper.core.interfaces import ReviewFiles
from clipper.core.judging import (
    is_own_label,
    is_own_rejection,
    mark_clip,
    save_judgment,
    trash_judgment,
)
from clipper.domain.errors import JudgingError, ReviewError
from clipper.domain.models import LabeledVideo, ManifestClip, RenderManifest

MAX_TITLE = 200


class BadRequest(Exception):
    """The request body is not a usable judgment (HTTP 400)."""


class UnknownClip(Exception):
    """No clip in the manifest has that rank (HTTP 404)."""


@dataclass(frozen=True)
class SaveRequest:
    start: float
    end: float
    title: str | None
    note: str | None


def _text(data: dict[str, object], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise BadRequest(f"{key} must be text")
    return value.strip() or None


def _seconds(data: dict[str, object], key: str) -> float:
    value = data.get(key)
    # bool is an int in Python, and true is not a time.
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BadRequest(f"{key} must be a number of seconds")
    if not math.isfinite(value):
        raise BadRequest(f"{key} must be a number of seconds")
    return float(value)


def parse_save(data: object, duration: float) -> SaveRequest:
    """Check a Save body: times inside the video, end after start, a one-line
    title of at most `MAX_TITLE` characters, and any note. Empty text is None.
    """
    if not isinstance(data, dict):
        raise BadRequest("the body must be a JSON object")
    start = _seconds(data, "start")
    end = _seconds(data, "end")
    if start < 0 or end > duration:
        raise BadRequest("the span must be inside the video")
    if end <= start:
        raise BadRequest("the end must be after the start")
    title = _text(data, "title")
    if title is not None and ("\n" in title or "\r" in title):
        raise BadRequest("the title must be one line")
    if title is not None and len(title) > MAX_TITLE:
        raise BadRequest(f"the title must be at most {MAX_TITLE} characters")
    return SaveRequest(start, end, title, _text(data, "note"))


def parse_trash(data: object) -> str | None:
    """Check a Trash body and return its note."""
    if not isinstance(data, dict):
        raise BadRequest("the body must be a JSON object")
    return _text(data, "note")


class ReviewStore:
    """The current manifest and judged labels of one review run."""

    def __init__(
        self,
        folder: Path,
        manifest: RenderManifest,
        labels: LabeledVideo | None,
        labels_path: Path,
        files: ReviewFiles,
    ) -> None:
        self._folder = folder
        self._labels_path = labels_path
        self._files = files
        self._lock = threading.Lock()
        self.manifest = manifest
        self.labels = labels

    def _clip(self, rank: int) -> ManifestClip:
        for clip in self.manifest.clips:
            if clip.rank == rank:
                return clip
        raise UnknownClip(rank)

    def judgment(self, clip: ManifestClip) -> dict[str, object] | None:
        """The clip's label or rejected span, as the page shows it."""
        if self.labels is None:
            return None
        for label in self.labels.labels:
            if is_own_label(label, clip):
                return {
                    "verdict": label.verdict,
                    "start": label.start_seconds,
                    "end": label.end_seconds,
                    "note": label.note,
                }
        for span in self.labels.rejected:
            if is_own_rejection(span, clip):
                return {"verdict": "trashed", "note": span.note}
        return None

    def save(self, rank: int, request: SaveRequest) -> ManifestClip:
        """Write the clip's label, then its title and `saved` in the manifest."""
        with self._lock:
            clip = self._clip(rank)
            if clip.reviewed == "trashed":
                raise JudgingError(
                    "This clip was trashed and its file deleted; saving it "
                    "needs a re-render, which is not built yet"
                )
            labels = save_judgment(
                self.labels,
                self.manifest.source,
                clip,
                request.start,
                request.end,
                request.note,
            )
            self._files.save_labels(labels, self._labels_path)
            self.labels = labels
            manifest = mark_clip(
                self.manifest, rank, reviewed="saved", title=request.title
            )
            self._files.save_manifest(manifest, self._folder)
            self.manifest = manifest
            return self._clip(rank)

    def trash(self, rank: int, note: str | None) -> ManifestClip:
        """Write a rejected span, delete the clip's file, then mark it trashed.

        Each step can be repeated, so a Trash that failed part way (a file
        held open by a player on Windows) can be retried.
        """
        with self._lock:
            clip = self._clip(rank)
            labels = trash_judgment(self.labels, self.manifest.source, clip, note)
            self._files.save_labels(labels, self._labels_path)
            self.labels = labels
            if clip.file is not None:
                if Path(clip.file).name != clip.file:
                    raise ReviewError(f"Not deleting {clip.file!r}: not a file name")
                try:
                    (self._folder / clip.file).unlink(missing_ok=True)
                except OSError as exc:
                    raise ReviewError(f"Cannot delete {clip.file}: {exc}") from exc
            manifest = mark_clip(self.manifest, rank, reviewed="trashed", file=None)
            self._files.save_manifest(manifest, self._folder)
            self.manifest = manifest
            return self._clip(rank)
