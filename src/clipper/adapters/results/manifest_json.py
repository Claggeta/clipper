from pathlib import Path

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
