import os
from pathlib import Path

from pydantic import ValidationError

from clipper.domain.errors import ManifestError
from clipper.domain.models import RenderManifest

MANIFEST_NAME = "manifest.json"


def save_manifest(manifest: RenderManifest, folder: Path) -> Path:
    """Write `manifest.json` in `folder`, replacing any earlier one.

    See docs/decisions/0019. Returns the path written.
    """
    path = folder / MANIFEST_NAME
    try:
        folder.mkdir(parents=True, exist_ok=True)
        # Bytes, so the file is UTF-8 with LF line endings on every platform.
        path.write_bytes(manifest.model_dump_json(indent=2).encode("utf-8") + b"\n")
    except OSError as exc:
        raise ManifestError(f"Cannot write manifest {path}: {exc}") from exc
    return path


def load_manifest(folder: Path) -> RenderManifest:
    """Read `manifest.json` from a folder of drafts. See docs/decisions/0019."""
    path = folder / MANIFEST_NAME
    if not path.is_file():
        raise ManifestError(f"Manifest not found: {path}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ManifestError(f"Cannot read manifest {path}: {exc}") from exc
    try:
        return RenderManifest.model_validate_json(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        if first["type"] == "json_invalid":
            detail = first["msg"].removeprefix("Invalid JSON: ")
            raise ManifestError(f"Invalid JSON in manifest {path}: {detail}") from exc
        location = ".".join(str(part) for part in first["loc"]) or "<root>"
        raise ManifestError(
            f"{path} is not a valid manifest: {location}: {first['msg']}"
        ) from exc


def check_manifest_source(manifest: RenderManifest, video: Path) -> None:
    """Fail if the manifest was written for a differently named video.

    Compared like `check_source_matches`: by name, case-insensitive on Windows.
    """
    if os.path.normcase(manifest.source) != os.path.normcase(video.name):
        raise ManifestError(
            f"Manifest is for {manifest.source!r} but the video is {video.name!r}"
        )
