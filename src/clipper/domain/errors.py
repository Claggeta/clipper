class ClipperError(Exception):
    """Base class for all expected Clipper errors."""


class AudioExtractionError(ClipperError):
    """Raised when audio cannot be extracted from a media file."""


class NoAudioStreamError(AudioExtractionError):
    """Raised when a media file contains no audio stream to extract."""


class TranscriptionError(ClipperError):
    """Raised when a transcript cannot be produced."""


class MediaProbeError(ClipperError):
    """Raised when a media file cannot be probed."""


class FrameError(ClipperError):
    """Raised when a still frame cannot be taken from a video."""


class AnalysisResultError(ClipperError):
    """Raised when a saved analysis result cannot be loaded or does not match."""


class ClipRenderError(ClipperError):
    """Raised when a clip cannot be rendered or its output path is unusable."""


class ManifestError(ClipperError):
    """Raised when a render manifest cannot be loaded or written."""


class ReviewError(ClipperError):
    """Raised when the review page cannot be served."""


class JudgingError(ClipperError):
    """Raised when a judgment in the review page conflicts with an earlier one."""


class LabelsError(ClipperError):
    """Raised when a labels file cannot be loaded."""


class DetectionError(ClipperError):
    """Raised when a detector cannot score a transcript."""


class LlmError(ClipperError):
    """Raised when a request to a local LLM fails."""


class TitleError(ClipperError):
    """Raised when a title and hook cannot be written for a clip."""


class DownloadError(ClipperError):
    """Raised when a URL is not fetched or a video cannot be downloaded."""


class FaceError(ClipperError):
    """Raised when faces cannot be found in a video."""


class SourceListError(ClipperError):
    """Raised when a `drafts --list` file cannot be used."""


class QueueError(ClipperError):
    """Raised when the post queue or the posts record cannot be built or read."""


class RemuxError(ClipperError):
    """Raised when a video cannot be remuxed to start at 0 (ADR 0020)."""
