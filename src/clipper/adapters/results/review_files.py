from pathlib import Path

from clipper.adapters.results.channels_txt import save_channel_cam
from clipper.adapters.results.labels_json import load_labels, save_labels
from clipper.adapters.results.manifest_json import save_manifest
from clipper.domain.models import FacecamBox, LabeledVideo, RenderManifest


class JsonReviewFiles:
    """The review page's judged labels and manifest, as the existing JSON files.

    See docs/decisions/0024 (item 3c amendment).
    """

    def load_labels(self, path: Path) -> LabeledVideo | None:
        return load_labels(path) if path.exists() else None

    def save_labels(self, labeled: LabeledVideo, path: Path) -> None:
        save_labels(labeled, path)

    def save_manifest(self, manifest: RenderManifest, folder: Path) -> None:
        save_manifest(manifest, folder)

    def save_channel_cam(
        self, path: Path, channel: str, box: FacecamBox | None
    ) -> None:
        save_channel_cam(path, channel, box)
