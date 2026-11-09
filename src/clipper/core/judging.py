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


def _same(span: Span | ManifestClip, start: float, end: float) -> bool:
    """Same start and end, to the millisecond a labels file keeps."""
    return _ms(span.start_seconds) == _ms(start) and _ms(span.end_seconds) == _ms(end)


def _overlaps(span: Span, start: float, end: float) -> bool:
    return span.start_seconds < end and start < span.end_seconds


def _times(start: float, end: float) -> dict[str, str]:
    return {"start": format_timestamp(start), "end": format_timestamp(end)}


def is_own_label(label: Label, clip: ManifestClip) -> bool:
    """Whether `label` is the judgment of `clip`: found by the draft's span.

    A `post` label has the draft's span; a `fix` label keeps it in `draft`.
    An `added` label belongs to no draft.
    """
    draft = (clip.start_seconds, clip.end_seconds)
    if label.verdict == "post":
        return _same(label, *draft)
    if label.verdict == "fix" and label.draft is not None:
        return _same(label.draft, *draft)
    return False


def is_own_rejection(span: RejectedSpan, clip: ManifestClip) -> bool:
    """Whether a rejected span is the judgment of `clip`."""
    return _same(span, clip.start_seconds, clip.end_seconds)


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
) -> LabeledVideo:
    """The labels with `clip` saved as `post` (span unchanged) or `fix`.

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
    if _same(clip, start, end):
        fields["verdict"] = "post"
    else:
        fields["verdict"] = "fix"
        fields["draft"] = _times(clip.start_seconds, clip.end_seconds)
    labels.append(Label.model_validate(fields))
    labels.sort(key=lambda label: label.start_seconds)
    return LabeledVideo(source=source, labels=tuple(labels), rejected=tuple(rejected))


def trash_judgment(
    labeled: LabeledVideo | None,
    source: str,
    clip: ManifestClip,
    note: str | None,
) -> LabeledVideo:
    """The labels with `clip` rejected, replacing its earlier judgment."""
    labels, rejected = _without_clip(labeled, clip)
    rejected.append(
        RejectedSpan.model_validate(
            {**_times(clip.start_seconds, clip.end_seconds), "note": note}
        )
    )
    rejected.sort(key=lambda span: span.start_seconds)
    return LabeledVideo(source=source, labels=tuple(labels), rejected=tuple(rejected))


def mark_clip(manifest: RenderManifest, rank: int, **changes: object) -> RenderManifest:
    """The manifest with clip `rank` changed, validated again."""
    clips = tuple(
        ManifestClip.model_validate({**clip.model_dump(), **changes})
        if clip.rank == rank
        else clip
        for clip in manifest.clips
    )
    return manifest.model_copy(update={"clips": clips})
