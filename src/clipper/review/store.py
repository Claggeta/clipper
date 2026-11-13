"""What the review page has judged so far, and how a judgment is written.

The server holds one `ReviewStore` for the run. Each Save or Trash reads and
replaces the labels and manifest under one lock, since the server answers
requests on several threads; a Save that re-renders holds it for the render.
See docs/decisions/0024 (item 3c and 3d amendments).
"""

import logging
import math
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from clipper.core.captions import build_ass
from clipper.core.cuts import Cut, TimeMap, check_cuts, cut_crop
from clipper.core.framing import plan_face_crop
from clipper.core.interfaces import ClipRenderer, FaceTracker, ReviewFiles
from clipper.core.judging import (
    drop_clip,
    is_own_label,
    is_own_rejection,
    mark_clip,
    next_rank,
    remove_judgment,
    same_span,
    save_judgment,
    trash_judgment,
)
from clipper.core.naming import clip_file_name, variant_file_name
from clipper.domain.errors import ClipperError, JudgingError, ReviewError
from clipper.domain.models import (
    CaptionEdits,
    ClipCandidate,
    ClipVariant,
    CropPath,
    LabeledVideo,
    ManifestClip,
    OnScreenText,
    RenderManifest,
    Transcript,
)

logger = logging.getLogger(__name__)

MAX_TITLE = 200
# The styles a Save may keep (ADR 0025).
STYLES = ("fit", "face")
# A keeper's rating: worth posting, or for the backlog (ADR 0024).
RATINGS = ("good", "ok")
# The reason listed for a clip added in the page (ADR 0019, 3e amendment).
ADDED_REASON = "added in review"
# On-screen texts per clip (ADR 0030); a hook and a few lines.
MAX_TEXTS = 5
# A text's look, sent by the page when set (ADR 0030, styling amendment).
_TEXT_LOOK = ("colour", "look", "size", "fade", "x", "y")
# A thumbnail's text is shown from time 0 of its one frame; any length will do.
THUMBNAIL_TEXT_SECONDS = 10.0
# No speech, for texts alone in a folder without captions.
_SILENT = Transcript(segments=())


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
    # The vertical style to keep; None keeps the clip's own (ADR 0025).
    style: str | None = None
    # The rating and who is on screen (ADR 0024, rating amendment).
    rating: str | None = None
    people: tuple[str, ...] = ()
    # Source-time ranges left out of the clip (ADR 0029).
    cuts: tuple[Cut, ...] = ()
    # Lines burned over the clip (ADR 0030).
    texts: tuple[OnScreenText, ...] = ()
    # Word fixes, key words and colour; None for the transcript's (ADR 0017).
    caption_edits: CaptionEdits | None = None


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


def parse_save(
    data: object, duration: float, people: tuple[str, ...] = ()
) -> SaveRequest:
    """Check a Save body: times inside the video, end after start, a one-line
    title of at most `MAX_TITLE` characters, and any note. Empty text is None.
    A rating is one of `RATINGS`; people are names from `people`, the run's
    list, kept in that list's order. Cuts are `[start, end]` pairs in order,
    apart and strictly inside the span (ADR 0029). Texts are objects of
    `text`, `start`, `end` (seconds of the finished clip) and `place`
    (ADR 0030). Caption edits are `fixes` of `start_seconds` and `text`,
    `keys` and a `highlight` (ADR 0017); none at all is None.
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
    style = data.get("style")
    if style is not None and style not in STYLES:
        raise BadRequest(f"the style must be one of {', '.join(STYLES)}")
    rating = data.get("rating")
    if rating is not None and rating not in RATINGS:
        raise BadRequest(f"the rating must be one of {', '.join(RATINGS)}")
    named = data.get("people", [])
    if not isinstance(named, list) or not all(isinstance(n, str) for n in named):
        raise BadRequest("people must be a list of names")
    unknown = [name for name in named if name not in people]
    if unknown:
        raise BadRequest(f"{unknown[0]!r} is not in the people list")
    chosen = tuple(name for name in people if name in named)
    cuts = _cuts(data.get("cuts", []))
    try:
        check_cuts(cuts, start, end)
    except ClipperError as exc:
        raise BadRequest(str(exc)) from exc
    texts = _texts(data.get("texts", []))
    return SaveRequest(
        start,
        end,
        title,
        _text(data, "note"),
        style,
        rating,
        chosen,
        cuts,
        texts,
        parse_caption_edits(data.get("caption_edits")),
    )


def parse_caption_edits(value: object) -> CaptionEdits | None:
    """Caption edits from a request, or None when absent or changing
    nothing. Raises `BadRequest` for anything the model refuses."""
    if value is None:
        return None
    try:
        edits = CaptionEdits.model_validate(value)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        message = first["msg"].removeprefix("Value error, ")
        raise BadRequest(
            f"caption_edits: {where}: {message}".replace(": :", ":")
        ) from exc
    return None if edits.empty else edits


def _texts(value: object) -> tuple[OnScreenText, ...]:
    if not isinstance(value, list) or len(value) > MAX_TEXTS:
        raise BadRequest(f"texts must be a list of at most {MAX_TEXTS}")
    texts: list[OnScreenText] = []
    for item in value:
        if not isinstance(item, dict):
            raise BadRequest("each text must be an object")
        try:
            texts.append(
                OnScreenText.model_validate(
                    {
                        "text": item.get("text"),
                        "start_seconds": _seconds(item, "start"),
                        "end_seconds": _seconds(item, "end"),
                        "place": item.get("place", "top"),
                        # Its look; absent is the original (ADR 0030).
                        **{
                            name: item[name]
                            for name in _TEXT_LOOK
                            if item.get(name) is not None
                        },
                    }
                )
            )
        except ValidationError as exc:
            message = exc.errors()[0]["msg"].removeprefix("Value error, ")
            raise BadRequest(f"text: {message}") from exc
    return tuple(texts)


def _cuts(value: object) -> tuple[Cut, ...]:
    if not isinstance(value, list):
        raise BadRequest("cuts must be a list of [start, end] pairs")
    cuts: list[Cut] = []
    for pair in value:
        if not isinstance(pair, list) or len(pair) != 2:
            raise BadRequest("cuts must be a list of [start, end] pairs")
        a, b = (_seconds({"cut": x}, "cut") for x in pair)
        cuts.append((a, b))
    return tuple(cuts)


@dataclass(frozen=True)
class ThumbnailRequest:
    """A thumbnail at a moment of the source, with optional text (ADR 0030)."""

    at: float
    text: OnScreenText | None
    # The style shown in the page; None is the clip's own (ADR 0025).
    style: str | None = None


def parse_thumbnail(data: object, duration: float) -> ThumbnailRequest:
    """Check a Thumbnail body: `at` inside the video, an optional style, and
    an optional one-line `text` with its `place`."""
    if not isinstance(data, dict):
        raise BadRequest("the body must be a JSON object")
    at = _seconds(data, "at")
    if not 0 <= at < duration:
        raise BadRequest("the moment must be inside the video")
    style = data.get("style")
    if style is not None and style not in STYLES:
        raise BadRequest(f"the style must be one of {', '.join(STYLES)}")
    words = _text(data, "text")
    if words is None:
        return ThumbnailRequest(at, None, style)
    try:
        text = OnScreenText.model_validate(
            {
                "text": words,
                "start_seconds": 0.0,
                "end_seconds": THUMBNAIL_TEXT_SECONDS,
                "place": data.get("place", "middle"),
            }
        )
    except ValidationError as exc:
        message = exc.errors()[0]["msg"].removeprefix("Value error, ")
        raise BadRequest(f"text: {message}") from exc
    return ThumbnailRequest(at, text, style)


def edits_payload(edits: CaptionEdits | None) -> dict[str, object] | None:
    """Caption edits as the page sends and reads them, or None."""
    return None if edits is None else edits.model_dump()


def thumbnail_name(clip_file: str) -> str:
    """`<clip stem>.thumb.jpg`, the same for each style of a clip."""
    stem = Path(clip_file).stem.removesuffix(".face")
    return f"{stem}.thumb.jpg"


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
    transcript for captions (None when the folder has no captions). A `face`
    re-render also needs the face tracker and the frame's width and height."""

    video: Path
    renderer: ClipRenderer
    transcript: Transcript | None
    face_tracker: FaceTracker | None = None
    frame_size: tuple[int, int] | None = None


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
                    "style": label.style,
                    "rating": label.rating,
                    "people": list(label.people),
                    "cuts": [[c.start_seconds, c.end_seconds] for c in label.cuts],
                    "texts": [text_payload(t) for t in label.texts],
                    "caption_edits": edits_payload(label.caption_edits),
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
            return self._save(self._clip(rank), request, self.manifest)

    def add(self, request: SaveRequest) -> SaveResult:
        """Save a moment no draft proposed as a new clip, ranked after the rest.

        Its label is `added`; it is rendered like any Save. Nothing is written
        if the span overlaps a label or its file name is taken.
        """
        with self._lock:
            clip = ManifestClip(
                rank=next_rank(self.manifest),
                start_seconds=request.start,
                end_seconds=request.end,
                score=0.0,
                reasons=(ADDED_REASON,),
                # Replaced by the file, or by the render's own error.
                error="not rendered yet",
                added=True,
            )
            base = self.manifest.model_copy(
                update={"clips": (*self.manifest.clips, clip)}
            )
            return self._save(clip, request, base)

    def _save(
        self, clip: ManifestClip, request: SaveRequest, base: RenderManifest
    ) -> SaveResult:
        """Save `clip` as listed in `base`; the caller holds the lock."""
        span = (request.start, request.end)
        needs_render = (
            clip.file is None
            or not same_span((clip.start_seconds, clip.end_seconds), span)
            or not _same_cuts(clip.cuts, request.cuts)
            or clip.texts != request.texts
            or (self.manifest.captions and clip.caption_edits != request.caption_edits)
        )
        candidate = ClipCandidate(
            start_seconds=request.start,
            end_seconds=request.end,
            score=clip.score,
            reasons=clip.reasons,
        )
        style = self._style(clip, request.style)
        name = clip_file_name(self._render.video, candidate)
        if style is not None:
            name = variant_file_name(name, style)
        if needs_render:
            self._check_name(clip, name)
        labels = save_judgment(
            self.labels,
            self.manifest.source,
            clip,
            request.start,
            request.end,
            request.note,
            style,
            request.rating,
            request.people,
            request.cuts,
            request.texts,
            request.caption_edits,
        )
        self._files.save_labels(labels, self._labels_path)
        self.labels = labels

        changes: dict[str, object] = {
            "reviewed": "saved",
            "title": request.title,
            "caption_edits": request.caption_edits,
            **_first_write(clip),
        }
        render_error = warning = None
        if needs_render:
            try:
                changes |= self._rerender(
                    clip,
                    candidate,
                    name,
                    style,
                    request.cuts,
                    request.texts,
                    request.caption_edits,
                )
            except ClipperError as exc:
                render_error = str(exc)
                if clip.file is None:
                    changes["error"] = render_error
            else:
                warning = self._delete_others(clip, name, "Re-rendered")
                changes["variants"] = _kept(style, name)
        elif style is not None:
            # The same span in the kept style: the other styles go (ADR 0025).
            kept = next(v.file for v in clip.variants if v.style == style)
            warning = self._delete_others(clip, kept, "Saved")
            changes |= {"file": kept, "variants": _kept(style, kept)}
        if clip.added:
            # An added clip's own label is found by its last saved span.
            changes["draft_start_seconds"] = request.start
            changes["draft_end_seconds"] = request.end
        manifest = mark_clip(base, clip.rank, **changes)
        self._files.save_manifest(manifest, self._folder)
        self.manifest = manifest
        return SaveResult(self._clip(clip.rank), render_error, warning)

    def _check_name(self, clip: ManifestClip, name: str) -> None:
        for other in self.manifest.clips:
            if other.rank != clip.rank and other.file == name:
                raise JudgingError(
                    f"The new span's file name {name} is clip {other.rank}'s; "
                    "move a handle by a second"
                )

    def _style(self, clip: ManifestClip, wanted: str | None) -> str | None:
        """The style the Save keeps: the one asked for, else the clip's own
        (its first variant); None for a clip without styles."""
        styles = [v.style for v in clip.variants]
        if wanted is None:
            return styles[0] if styles else None
        if wanted not in styles:
            raise JudgingError(f"This clip has no {wanted} draft to keep")
        return wanted

    def _crop(self, candidate: ClipCandidate) -> CropPath:
        """The face style's crop for a new span (ADR 0025)."""
        tracker, size = self._render.face_tracker, self._render.frame_size
        if tracker is None or size is None:
            raise ReviewError("A face re-render needs the face tracker and frame size")
        track = tracker.track(
            self._render.video, candidate.start_seconds, candidate.end_seconds
        )
        crop = plan_face_crop(track, *size)
        if crop is None:
            raise ReviewError("No face in the new span to frame in the face style")
        return crop

    def _rerender(
        self,
        clip: ManifestClip,
        candidate: ClipCandidate,
        name: str,
        style: str | None,
        cuts: tuple[Cut, ...] = (),
        texts: tuple[OnScreenText, ...] = (),
        caption_edits: CaptionEdits | None = None,
    ) -> dict[str, object]:
        """Render to a temporary file, then rename it into place.

        Returns the manifest changes. A failure leaves no temporary file and
        any old file untouched. With cuts, the face crop and captions follow
        the cut clip's times (ADR 0029). Texts are burned with the captions,
        or alone in a folder without them (ADR 0030).
        """
        manifest = self.manifest
        time_map = TimeMap(candidate.start_seconds, candidate.end_seconds, cuts)
        crop = cut_crop(self._crop(candidate), time_map) if style == "face" else None
        ass = ""
        burned: str | None = None
        captioned = manifest.captions and self._render.transcript is not None
        if captioned or texts:
            built = build_ass(
                self._render.transcript
                if captioned and self._render.transcript
                else _SILENT,
                candidate,
                vertical=manifest.vertical,
                cuts=cuts,
                texts=texts,
                edits=caption_edits,
            )
            ass = built.text
            if built.speech:
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
                crop=crop,
                cuts=cuts,
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
            "cuts": cuts,
            "texts": texts,
        }

    def _delete_others(self, clip: ManifestClip, keep: str, done: str) -> str | None:
        """Delete the clip's files other than `keep`: its previous file and its
        other styles. Returns a message for the first one not deleted."""
        names = [clip.file] if clip.file is not None else []
        names += [v.file for v in clip.variants]
        warning = None
        for name in dict.fromkeys(names):
            if name == keep:
                continue
            if Path(name).name != name:
                warning = warning or f"{done}; not deleting {name!r}: not a file name"
                continue
            try:
                (self._folder / name).unlink(missing_ok=True)
            except OSError as exc:
                warning = warning or (
                    f"{done}, but the old file {name} was not deleted: {exc}"
                )
        return warning

    def thumbnail(
        self, rank: int, request: ThumbnailRequest
    ) -> tuple[ManifestClip, str | None]:
        """Write the clip's thumbnail at `request.at`, in the style it keeps,
        replacing any earlier one, and record it (ADR 0030).

        Returns the clip and a note: a face clip with no face at that moment
        is framed as `fit`. Raises `JudgingError` for a clip without a file.
        """
        with self._lock:
            clip = self._clip(rank)
            if clip.file is None:
                raise JudgingError("Save the clip before making its thumbnail")
            manifest = self.manifest
            style = self._style(clip, request.style)
            crop = None
            note = None
            if style == "face":
                try:
                    crop = self._crop(
                        ClipCandidate(
                            start_seconds=request.at,
                            end_seconds=request.at + 1.0,
                            score=0.0,
                            reasons=("thumbnail",),
                        )
                    )
                except ReviewError:
                    note = "No face at that moment; framed as Fit."
            ass = None
            if request.text is not None:
                ass = build_ass(
                    _SILENT,
                    ClipCandidate(
                        start_seconds=0.0,
                        end_seconds=THUMBNAIL_TEXT_SECONDS,
                        score=0.0,
                        reasons=("thumbnail",),
                    ),
                    vertical=manifest.vertical,
                    texts=(request.text,),
                ).text
            name = thumbnail_name(clip.file)
            target = self._folder / name
            temp = target.with_name(f"{target.stem}.part.jpg")
            try:
                self._render.renderer.still(
                    self._render.video,
                    request.at,
                    temp,
                    captions=ass or None,
                    vertical=manifest.vertical,
                    crop=crop,
                )
                try:
                    os.replace(temp, target)
                except OSError as exc:
                    raise ReviewError(f"Cannot replace {name}: {exc}") from exc
            finally:
                temp.unlink(missing_ok=True)
            manifest = mark_clip(manifest, rank, thumbnail=name)
            self._files.save_manifest(manifest, self._folder)
            self.manifest = manifest
            return self._clip(rank), note

    def trash(self, rank: int, note: str | None) -> ManifestClip | None:
        """Write a rejected span, delete the clip's file, then mark it trashed.

        A clip added in the page was never a draft, so it is removed instead:
        its label, its file and its manifest entry; then None is returned.
        Each step can be repeated, so a Trash that failed part way (a file
        held open by a player on Windows) can be retried.
        """
        with self._lock:
            clip = self._clip(rank)
            source = self.manifest.source
            if clip.added:
                labels = remove_judgment(self.labels, source, clip)
            else:
                labels = trash_judgment(self.labels, source, clip, note)
            self._files.save_labels(labels, self._labels_path)
            self.labels = labels
            self._delete_file(clip)
            # A style file left behind does not stop the trash (ADR 0025).
            if warning := self._delete_variants(clip):
                logger.warning("%s", warning)
            if warning := self._delete_thumbnail(clip):
                logger.warning("%s", warning)
            if clip.added:
                manifest = drop_clip(self.manifest, rank)
            else:
                manifest = mark_clip(
                    self.manifest,
                    rank,
                    reviewed="trashed",
                    file=None,
                    variants=(),
                    thumbnail=None,
                    **_first_write(clip),
                )
            self._files.save_manifest(manifest, self._folder)
            self.manifest = manifest
            return None if clip.added else self._clip(rank)

    def _delete_variants(self, clip: ManifestClip) -> str | None:
        """Delete the clip's other style files (ADR 0025); a message if not."""
        for variant in clip.variants:
            if variant.file == clip.file:
                continue
            if Path(variant.file).name != variant.file:
                return f"Not deleting {variant.file!r}: not a file name"
            try:
                (self._folder / variant.file).unlink(missing_ok=True)
            except OSError as exc:
                return f"The {variant.style} file {variant.file} was not deleted: {exc}"
        return None

    def _delete_thumbnail(self, clip: ManifestClip) -> str | None:
        """Delete the clip's thumbnail, if any; a message if not."""
        name = clip.thumbnail
        if name is None:
            return None
        if Path(name).name != name:
            return f"Not deleting {name!r}: not a file name"
        try:
            (self._folder / name).unlink(missing_ok=True)
        except OSError as exc:
            return f"The thumbnail {name} was not deleted: {exc}"
        return None

    def _delete_file(self, clip: ManifestClip) -> None:
        if clip.file is None:
            return
        if Path(clip.file).name != clip.file:
            raise ReviewError(f"Not deleting {clip.file!r}: not a file name")
        try:
            (self._folder / clip.file).unlink(missing_ok=True)
        except OSError as exc:
            raise ReviewError(f"Cannot delete {clip.file}: {exc}") from exc


def text_payload(text: OnScreenText) -> dict[str, object]:
    """A text as the page sends and shows it."""
    return {
        "text": text.text,
        "start": text.start_seconds,
        "end": text.end_seconds,
        "place": text.place,
        "colour": text.colour,
        "look": text.look,
        "size": text.size,
        "fade": text.fade,
        "x": text.x,
        "y": text.y,
    }


def _same_cuts(a: tuple[Cut, ...], b: tuple[Cut, ...]) -> bool:
    """The same cuts, compared to the millisecond as labels keep them."""
    return len(a) == len(b) and all(same_span(x, y) for x, y in zip(a, b, strict=True))


def _kept(style: str | None, file: str) -> tuple[ClipVariant, ...]:
    """A saved clip's variants: the one style kept, or none (ADR 0025)."""
    if style is None:
        return ()
    return (ClipVariant.model_validate({"style": style, "file": file}),)
