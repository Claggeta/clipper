import json
import os
from pathlib import Path

from pydantic import ValidationError

from clipper.core.labeling import parse_links
from clipper.domain.errors import LabelsError
from clipper.domain.models import (
    AnalysisResult,
    ClipLink,
    LabeledVideo,
    Span,
    format_timestamp,
)


def load_labels(path: Path) -> LabeledVideo:
    """Load a hand-written labels file. See docs/decisions/0012."""
    if not path.is_file():
        raise LabelsError(f"Labels file not found: {path}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise LabelsError(f"Cannot read labels file {path}: {exc}") from exc

    try:
        return LabeledVideo.model_validate_json(raw)
    except ValidationError as exc:
        errors = exc.errors()
        first = errors[0]
        if first["type"] == "json_invalid":
            detail = first["msg"].removeprefix("Invalid JSON: ")
            raise LabelsError(f"Invalid JSON in labels file {path}: {detail}") from exc
        location = ".".join(str(part) for part in first["loc"]) or "<root>"
        more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
        raise LabelsError(
            f"{path} is not a valid labels file: {location}: {first['msg']}{more}"
        ) from exc


def save_labels(labeled: LabeledVideo, path: Path) -> None:
    """Write a labels file in the hand-written format, times as "H:MM:SS".

    Overwrites `path`; the caller decides whether that is allowed. Verdicts,
    drafts and rejected spans are written only when present.
    """
    labels = []
    for label in labeled.labels:
        entry: dict[str, object] = _span(label)
        if label.verdict is not None:
            entry["verdict"] = label.verdict
        if label.draft is not None:
            entry["draft"] = _span(label.draft)
        if label.note is not None:
            entry["note"] = label.note
        labels.append(entry)
    data: dict[str, object] = {"source": labeled.source, "labels": labels}
    if labeled.rejected:
        rejected = []
        for span in labeled.rejected:
            entry = _span(span)
            if span.note is not None:
                entry["note"] = span.note
            rejected.append(entry)
        data["rejected"] = rejected
    text = json.dumps(data, indent=2, ensure_ascii=False)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8", newline="\n")
    except OSError as exc:
        raise LabelsError(f"Cannot write labels file {path}: {exc}") from exc


def _span(span: Span) -> dict[str, object]:
    return {
        "start": format_timestamp(span.start_seconds),
        "end": format_timestamp(span.end_seconds),
    }


def load_links(path: Path) -> list[ClipLink]:
    """Read a links file of Twitch clips. See docs/decisions/0012."""
    if not path.is_file():
        raise LabelsError(f"Links file not found: {path}")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise LabelsError(f"Cannot read links file {path}: {exc}") from exc
    return parse_links(text, str(path))


def check_labels_match(labeled: LabeledVideo, result: AnalysisResult) -> None:
    """Fail if the labels were written for a differently named video.

    Compared like `check_source_matches`: by name, case-insensitive on Windows.
    """
    if os.path.normcase(labeled.source) != os.path.normcase(result.source):
        raise LabelsError(
            f"Labels are for {labeled.source!r} but the analysis result is for "
            f"{result.source!r}"
        )
