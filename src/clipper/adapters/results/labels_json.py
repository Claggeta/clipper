from pathlib import Path

from pydantic import ValidationError

from clipper.domain.errors import LabelsError
from clipper.domain.models import LabeledVideo


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
