"""What the review page has judged so far, and how a judgment is written.

The server holds one `ReviewStore` for the run. Each Save or Trash reads and
replaces the labels and manifest under one lock, since the server answers
requests on several threads; a Save that re-renders holds it for the render.
See docs/decisions/0024 (item 3c and 3d amendments).
"""

import math
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from clipper.core.captions import build_ass
from clipper.core.interfaces import ClipRenderer, ReviewFiles
from clipper.core.judging import (
    is_own_label,
    is_own_rejection,
    mark_clip,
    same_span,
    save_judgment,
    trash_judgment,
)
from clipper.core.naming import clip_file_name
from clipper.domain.errors import ClipperError, JudgingError, ReviewError
from clipper.domain.models import (
    ClipCandidate,
    LabeledVideo,
    ManifestClip,
    RenderManifest,
    Transcript,
)

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


def _first_write(clip: ManifestClip) -> dict[str, object]:
    """On the page's first write to a clip, keep the model's title."""
    return {"model_title": clip.title} if clip.reviewed is None else {}


@dataclass(frozen=True)
class SaveResult:
    """The clip after a Save, and what went wrong after its label was written.

    `render_error` is a failed re-render; the label stands. `warning` is an
    old file that could not be deleted after a good re-render.
    """

    clip: ManifestClip
    render_error: str | None = None
    warning: str | None = None


@dataclass(frozen=True)
class RenderInputs:
    """What a re-render needs: the source video, the renderer, and the
    transcript for captions (None when the folder has no captions)."""

    video: Path
    renderer: ClipRenderer
    transcript: Transcript | None


class ReviewStore:
    """The current manifest and judged labels of one review run."""

    def __init__(
        self,
        folder: Path,
        manifest: RenderManifest,
        labels: LabeledVideo | None,
        labels_path: Path,
        files: ReviewFiles,
        render: RenderInputs,
    ) -> None:
        self._folder = folder
        self._labels_path = labels_path
        self._files = files
        self._render = render
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

    def save(self, rank: int, request: SaveRequest) -> SaveResult:
        """Write the clip's label, re-render it if its video changed, then
        update the manifest.

        The video changed when the span differs from what the file covers, or
        there is no file (trashed, or its render failed). The label is written
        first and stands if the render fails: a clip with a file keeps it and
        its span; one without records the error.
        """
        with self._lock:
            clip = self._clip(rank)
            span = (request.start, request.end)
            needs_render = clip.file is None or not same_span(
                (clip.start_seconds, clip.end_seconds), span
            )
            candidate = ClipCandidate(
                start_seconds=request.start,
                end_seconds=request.end,
                score=clip.score,
                reasons=clip.reasons,
            )
            name = clip_file_name(self._render.video, candidate)
            if needs_render:
                self._check_name(clip, name)
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

            changes: dict[str, object] = {
                "reviewed": "saved",
                "title": request.title,
                **_first_write(clip),
            }
            render_error = warning = None
            if needs_render:
                try:
                    changes |= self._rerender(clip, candidate, name)
                except ClipperError as exc:
                    render_error = str(exc)
                    if clip.file is None:
                        changes["error"] = render_error
                else:
                    warning = self._delete_old(clip, name)
            manifest = mark_clip(self.manifest, rank, **changes)
            self._files.save_manifest(manifest, self._folder)
            self.manifest = manifest
            return SaveResult(self._clip(rank), render_error, warning)

    def _check_name(self, clip: ManifestClip, name: str) -> None:
        for other in self.manifest.clips:
            if other.rank != clip.rank and other.file == name:
                raise JudgingError(
                    f"The new span's file name {name} is clip {other.rank}'s; "
                    "move a handle by a second"
                )

    def _rerender(
        self, clip: ManifestClip, candidate: ClipCandidate, name: str
    ) -> dict[str, object]:
        """Render to a temporary file, then rename it into place.

        Returns the manifest changes. A failure leaves no temporary file and
        any old file untouched.
        """
        manifest = self.manifest
        ass = ""
        burned: str | None = None
        if manifest.captions and self._render.transcript is not None:
            built = build_ass(
                self._render.transcript, candidate, vertical=manifest.vertical
            )
            ass = built.text
            if ass:
                burned = "segments" if built.from_segments else "words"
        target = self._folder / name
        temp = target.with_name(f"{target.stem}.part.mp4")
        try:
            self._render.renderer.render(
                self._render.video,
                candidate,
                temp,
                captions=ass or None,
                vertical=manifest.vertical,
            )
            try:
                os.replace(temp, target)
            except OSError as exc:
                raise ReviewError(f"Cannot replace {name}: {exc}") from exc
        finally:
            temp.unlink(missing_ok=True)
        draft_start, draft_end = clip.draft_span
        return {
            "file": name,
            "error": None,
            "start_seconds": candidate.start_seconds,
            "end_seconds": candidate.end_seconds,
            "captions": burned,
            "draft_start_seconds": draft_start,
            "draft_end_seconds": draft_end,
        }

    def _delete_old(self, clip: ManifestClip, name: str) -> str | None:
        """Delete the clip's previous file if the new one has another name."""
        if clip.file is None or clip.file == name:
            return None
        if Path(clip.file).name != clip.file:
            return f"Re-rendered; not deleting {clip.file!r}: not a file name"
        try:
            (self._folder / clip.file).unlink(missing_ok=True)
        except OSError as exc:
            return f"Re-rendered, but the old file {clip.file} was not deleted: {exc}"
        return None

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
            manifest = mark_clip(
                self.manifest,
                rank,
                reviewed="trashed",
                file=None,
                **_first_write(clip),
            )
            self._files.save_manifest(manifest, self._folder)
            self.manifest = manifest
            return self._clip(rank)
