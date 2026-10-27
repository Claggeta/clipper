class ClipperError(Exception):
    """Base class for all expected Clipper errors."""


class AudioExtractionError(ClipperError):
    """Raised when audio cannot be extracted from a media file."""


class TranscriptionError(ClipperError):
    """Raised when a transcript cannot be produced."""
