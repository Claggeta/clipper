from pathlib import Path
from typing import Protocol, runtime_checkable

from clipper.domain.models import (
    ClipCandidate,
    ClipInfo,
    ClipTitle,
    LabeledVideo,
    MediaInfo,
    RenderedClip,
    RenderManifest,
    Transcript,
    VideoSource,
)


@runtime_checkable
class Transcriber(Protocol):
    """Turns a video or audio file into a Transcript."""

    def transcribe(self, media_path: Path) -> Transcript: ...


@runtime_checkable
class AudioExtractor(Protocol):
    """Extracts an audio track from a video file into a WAV file."""

    def extract(self, video_path: Path, output_path: Path) -> Path: ...


@runtime_checkable
class FrameExtractor(Protocol):
    """Saves one still frame of a video, at a time in seconds, as a JPEG.

    Creates the output's parent directory, overwrites an existing file,
    returns `output_path`, and raises `FrameError` on failure (see
    docs/decisions/0024, film strip amendment).
    """

    def extract(self, video_path: Path, seconds: float, output_path: Path) -> Path: ...


@runtime_checkable
class ReviewFiles(Protocol):
    """Reads and writes what the review page judges (docs/decisions/0024).

    `load_labels` returns None when the judged labels file does not exist
    yet. Failures raise `LabelsError` or `ManifestError`.
    """

    def load_labels(self, path: Path) -> LabeledVideo | None: ...

    def save_labels(self, labeled: LabeledVideo, path: Path) -> None: ...

    def save_manifest(self, manifest: RenderManifest, folder: Path) -> None: ...


@runtime_checkable
class ClipDetector(Protocol):
    """Finds clip-worthy time ranges in a transcript."""

    def detect(self, transcript: Transcript) -> list[ClipCandidate]: ...


@runtime_checkable
class MediaProbe(Protocol):
    """Reports structural facts about a media file."""

    def probe(self, media_path: Path) -> MediaInfo: ...


@runtime_checkable
class ClipRenderer(Protocol):
    """Renders one clip candidate from a video into a file.

    The caller chooses `output_path` (see `clipper.core.naming`). The renderer
    creates its parent directory, overwrites an existing file, returns a
    `RenderedClip` whose `path` is `output_path`, and raises `ClipRenderError`
    on failure.

    `captions` is ASS text with times relative to the clip start, laid out
    for the frame being rendered (see `clipper.core.captions`), burned into
    the video. `None` or "" renders without captions.

    `vertical` fits the whole frame into 9:16 at 1080x1920 over a blurred copy
    of itself, before any captions are drawn (see docs/decisions/0016).
    """

    def render(
        self,
        video_path: Path,
        candidate: ClipCandidate,
        output_path: Path,
        captions: str | None = None,
        *,
        vertical: bool = False,
    ) -> RenderedClip: ...


@runtime_checkable
class TitleWriter(Protocol):
    """Suggests a title and hook for one clip from its spoken text.

    `text` is what is said inside the clip (see `clipper.core.captions`), never
    empty. Raises `TitleError` on failure; there is no fallback text (see
    docs/decisions/0018).
    """

    def write(self, text: str) -> ClipTitle: ...


@runtime_checkable
class Downloader(Protocol):
    """Downloads one video into a folder.

    The file is `video_path(source, folder)` (see `clipper.core.sources`). The
    downloader creates the folder, returns that path, and raises
    `DownloadError` on failure (see docs/decisions/0020).
    """

    def download(self, source: VideoSource, folder: Path) -> Path: ...


@runtime_checkable
class ClipLookup(Protocol):
    """Reads one Twitch clip's title and length, without downloading it.

    `clip_id` is the clip's slug (see `clipper.core.labeling`). Raises
    `DownloadError` on failure (see docs/decisions/0020, clip lookups).
    """

    def lookup(self, clip_id: str) -> ClipInfo: ...
