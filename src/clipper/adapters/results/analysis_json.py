import os
from pathlib import Path

from pydantic import ValidationError

from clipper.domain.errors import AnalysisResultError
from clipper.domain.models import AnalysisResult


def load_analysis_result(path: Path) -> AnalysisResult:
    """Load the JSON that `analyze` saved. See docs/decisions/0009."""
    if not path.is_file():
        raise AnalysisResultError(f"Analysis result not found: {path}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AnalysisResultError(f"Cannot read analysis result {path}: {exc}") from exc

    try:
        return AnalysisResult.model_validate_json(raw)
    except ValidationError as exc:
        errors = exc.errors()
        first = errors[0]
        if first["type"] == "json_invalid":
            raise AnalysisResultError(
                f"Invalid JSON in analysis result {path}: {first['msg']}"
            ) from exc
        location = ".".join(str(part) for part in first["loc"]) or "<root>"
        more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
        raise AnalysisResultError(
            f"{path} is not a valid analysis result: {location}: {first['msg']}{more}"
        ) from exc


def check_source_matches(result: AnalysisResult, video: Path) -> None:
    """Fail if the result was saved for a differently named video.

    `normcase` follows the host filesystem: case-insensitive on Windows, exact
    elsewhere. It checks the name only, so it is a weak check (see ADR 0009).
    """
    if os.path.normcase(result.source) != os.path.normcase(video.name):
        raise AnalysisResultError(
            f"Analysis result is for {result.source!r} but the video is {video.name!r}"
        )
