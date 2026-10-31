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


class AnalysisResultError(ClipperError):
    """Raised when a saved analysis result cannot be loaded or does not match."""


class ClipRenderError(ClipperError):
    """Raised when a clip cannot be rendered or its output path is unusable."""
