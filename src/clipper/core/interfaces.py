from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from clipper.domain.models import (
    AudioMix,
    ClipCandidate,
    ClipInfo,
    ClipTitle,
    CropPath,
    FaceTrack,
    HandCropPlan,
    LabeledVideo,
    MediaInfo,
    RenderedClip,
    RenderManifest,
    Transcript,
    VideoInfo,
    VideoSource,
    Zoom,
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
class PeaksExtractor(Protocol):
    """Writes a video's audio levels for the review page's waveform.

    One byte per `1 / PEAKS_PER_SECOND` s, the loudest sample in it scaled
    to 0 to 255, to `output_path`; creates its parent directory, returns
    `output_path`, and raises `PeaksError` on failure (see
    docs/decisions/0031, precision amendment).
    """

    def extract(self, video_path: Path, output_path: Path) -> Path: ...


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
    `crop` is the face style instead: a full-height window that moves over
    time, scaled to 1080x1920 (see docs/decisions/0025).

    `cuts` are source-time ranges strictly inside the candidate's span to
    leave out, in order (ADR 0029); the kept parts are joined in the same
    encode. Captions and crop times are then times in the cut clip.

    `zooms` are punch-ins on the 9:16 frame, before captions, in the cut
    clip's seconds (ADR 0016, zoom amendment); `hand` the fit style's
    hand-placed windows (ADR 0025, hand crop amendment). Both need the
    vertical frame. `audio` is the clip's volume, music and sound effects,
    effect times in the cut clip's seconds (ADR 0007, audio amendment).
    """

    def render(
        self,
        video_path: Path,
        candidate: ClipCandidate,
        output_path: Path,
        captions: str | None = None,
        *,
        vertical: bool = False,
        crop: CropPath | None = None,
        cuts: tuple[tuple[float, float], ...] = (),
        zooms: tuple[Zoom, ...] = (),
        hand: HandCropPlan | None = None,
        audio: AudioMix | None = None,
    ) -> RenderedClip: ...

    def join(
        self,
        clips: Sequence[Path],
        output_path: Path,
        captions: str | None = None,
        *,
        audio: AudioMix | None = None,
        length: float = 0.0,
    ) -> Path:
        """Rendered clips joined end to end into `output_path`, with ASS
        `captions` timed on the joined clip and an audio mix as long as
        `length` (ADR 0032)."""
        ...

    def still(
        self,
        video_path: Path,
        seconds: float,
        output_path: Path,
        captions: str | None = None,
        *,
        vertical: bool = False,
        crop: CropPath | None = None,
    ) -> Path:
        """One frame at `seconds` of the source as a JPEG, framed as a clip
        is (fit, or the crop's first window) with `captions` burned in from
        time 0: a thumbnail (ADR 0030). Raises `ClipRenderError`."""
        ...


@runtime_checkable
class Remuxer(Protocol):
    """Rewrites a video in place so its timestamps start at 0, losslessly.

    Raises `RemuxError` and leaves the video as it was on failure (see
    docs/decisions/0020, remux amendment).
    """

    def remux(self, video_path: Path) -> None: ...


@runtime_checkable
class TitleWriter(Protocol):
    """Suggests a title and hook for one clip from its spoken text.

    `text` is what is said inside the clip (see `clipper.core.captions`), never
    empty. `video` is the source's metadata, if it was fetched (V5 item 8b),
    and `about` the user's `--about` text, if given (8c). Raises `TitleError`
    on failure; there is no fallback text (see docs/decisions/0018).
    """

    def write(
        self, text: str, video: VideoInfo | None = None, about: str | None = None
    ) -> ClipTitle: ...


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


@runtime_checkable
class VideoInfoLookup(Protocol):
    """Reads one video's metadata, without downloading it.

    Raises `DownloadError` on failure (see docs/decisions/0020, video info).
    """

    def lookup(self, source: VideoSource) -> VideoInfo: ...


@runtime_checkable
class FaceTracker(Protocol):
    """Finds faces across one span of a video, a few samples a second.

    Raises `FaceError` on failure (see docs/decisions/0025).
    """

    def track(
        self, video_path: Path, start_seconds: float, end_seconds: float
    ) -> FaceTrack: ...
