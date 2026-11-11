"""Judgments made in the review page, as labels and manifest changes.

Pure functions: given the judged labels so far, a manifest clip and what the
maintainer did, return the new labels or manifest. Writing them is the
caller's. See docs/decisions/0024 (item 3c amendment) and 0012 (judged
labels).
"""

import os
from pathlib import Path

from clipper.domain.errors import JudgingError, LabelsError
from clipper.domain.models import (
    Label,
    LabeledVideo,
    ManifestClip,
    RejectedSpan,
    RenderManifest,
    Span,
    format_timestamp,
)


def judged_labels_path(labels_dir: Path, source: str) -> Path:
    """`<labels dir>/picks/<stem>.json`, apart from clip labels (ADR 0012)."""
    return labels_dir / "picks" / f"{Path(source).stem}.json"


def check_judged_labels(labeled: LabeledVideo, source: str, path: Path) -> None:
    """Fail unless the file is for `source` and holds only judged labels.

    The name is compared like `check_source_matches`: case-insensitive on
    Windows. A file of labels without verdicts would mix with judged ones,
    which ADR 0012 rules out.
    """
    if os.path.normcase(labeled.source) != os.path.normcase(source):
        raise LabelsError(
            f"{path} is for {labeled.source!r}, but the drafts are for {source!r}"
        )
    if labeled.labels and not labeled.judged:
        raise LabelsError(
            f"{path} has labels without verdicts; judged labels cannot be added to it"
        )


def _ms(seconds: float) -> int:
    return round(seconds * 1000)


def same_span(a: tuple[float, float], b: tuple[float, float]) -> bool:
    """Same start and end, to the millisecond a labels file keeps."""
    return (_ms(a[0]), _ms(a[1])) == (_ms(b[0]), _ms(b[1]))


def _span(span: Span) -> tuple[float, float]:
    return span.start_seconds, span.end_seconds


def _overlaps(span: Span, start: float, end: float) -> bool:
    return span.start_seconds < end and start < span.end_seconds


def _times(start: float, end: float) -> dict[str, str]:
    return {"start": format_timestamp(start), "end": format_timestamp(end)}


def is_own_label(label: Label, clip: ManifestClip) -> bool:
    """Whether `label` is the judgment of `clip`: found by the draft's span.

    A `post` label has the draft's span; a `fix` label keeps it in `draft`.
    The draft's span survives a re-render in the manifest
    (`ManifestClip.draft_span`). An `added` label belongs only to a clip
    added in the page, whose draft span is its last saved span.
    """
    if clip.added:
        return label.verdict == "added" and same_span(_span(label), clip.draft_span)
    if label.verdict == "post":
        return same_span(_span(label), clip.draft_span)
    if label.verdict == "fix" and label.draft is not None:
        return same_span(_span(label.draft), clip.draft_span)
    return False


def is_own_rejection(span: RejectedSpan, clip: ManifestClip) -> bool:
    """Whether a rejected span is the judgment of `clip`; an added clip has
    none, since Trash removes it outright."""
    return not clip.added and same_span(_span(span), clip.draft_span)


def _without_clip(
    labeled: LabeledVideo | None, clip: ManifestClip
) -> tuple[list[Label], list[RejectedSpan]]:
    if labeled is None:
        return [], []
    labels = [label for label in labeled.labels if not is_own_label(label, clip)]
    rejected = [span for span in labeled.rejected if not is_own_rejection(span, clip)]
    return labels, rejected


def save_judgment(
    labeled: LabeledVideo | None,
    source: str,
    clip: ManifestClip,
    start: float,
    end: float,
    note: str | None,
    style: str | None = None,
    rating: str | None = None,
    people: tuple[str, ...] = (),
) -> LabeledVideo:
    """The labels with `clip` saved as `post` (span unchanged) or `fix`, or
    as `added` for a clip added in the page, whatever its span. `style` is the
    vertical style kept, if the clip had styles (ADR 0025); `rating` and
    `people` are the page's rating and who is on screen (ADR 0024).

    Replaces the clip's earlier label or rejected span. Raises `JudgingError`
    if the span overlaps another label: one label per moment.
    """
    labels, rejected = _without_clip(labeled, clip)
    for other in labels:
        if _overlaps(other, start, end):
            raise JudgingError(
                f"Overlaps the label {format_timestamp(other.start_seconds)}-"
                f"{format_timestamp(other.end_seconds)} ({other.verdict}); trim "
                "this span, or trash it as a duplicate"
            )
    fields: dict[str, object] = {**_times(start, end), "note": note}
    if style is not None:
        fields["style"] = style
    if rating is not None:
        fields["rating"] = rating
    if people:
        fields["people"] = people
    if clip.added:
        fields["verdict"] = "added"
    elif same_span(clip.draft_span, (start, end)):
        fields["verdict"] = "post"
    else:
        fields["verdict"] = "fix"
        fields["draft"] = _times(*clip.draft_span)
    labels.append(Label.model_validate(fields))
    labels.sort(key=lambda label: label.start_seconds)
    return LabeledVideo(source=source, labels=tuple(labels), rejected=tuple(rejected))


def trash_judgment(
    labeled: LabeledVideo | None,
    source: str,
    clip: ManifestClip,
    note: str | None,
) -> LabeledVideo:
    """The labels with `clip`'s draft rejected, replacing its earlier judgment."""
    labels, rejected = _without_clip(labeled, clip)
    rejected.append(
        RejectedSpan.model_validate({**_times(*clip.draft_span), "note": note})
    )
    rejected.sort(key=lambda span: span.start_seconds)
    return LabeledVideo(source=source, labels=tuple(labels), rejected=tuple(rejected))


def remove_judgment(
    labeled: LabeledVideo | None, source: str, clip: ManifestClip
) -> LabeledVideo:
    """The labels without `clip`'s judgment: Trash on a clip added in the page."""
    labels, rejected = _without_clip(labeled, clip)
    return LabeledVideo(source=source, labels=tuple(labels), rejected=tuple(rejected))


def next_rank(manifest: RenderManifest) -> int:
    """The rank for a clip added in the page: after every clip so far."""
    return max((clip.rank for clip in manifest.clips), default=0) + 1


def drop_clip(manifest: RenderManifest, rank: int) -> RenderManifest:
    """The manifest without clip `rank`."""
    clips = tuple(clip for clip in manifest.clips if clip.rank != rank)
    return manifest.model_copy(update={"clips": clips})


def mark_clip(manifest: RenderManifest, rank: int, **changes: object) -> RenderManifest:
    """The manifest with clip `rank` changed, validated again."""
    clips = tuple(
        ManifestClip.model_validate({**clip.model_dump(), **changes})
        if clip.rank == rank
        else clip
        for clip in manifest.clips
    )
    return manifest.model_copy(update={"clips": clips})
