import logging
from pathlib import Path

from clipper.core.interfaces import ClipDetector, Transcriber
from clipper.domain.errors import ClipperError
from clipper.domain.models import AnalysisResult

logger = logging.getLogger(__name__)


class AnalysisPipeline:
    """Video file -> transcript -> clip candidates."""

    def __init__(self, transcriber: Transcriber, detector: ClipDetector) -> None:
        self._transcriber = transcriber
        self._detector = detector

    def run(self, video_path: Path) -> AnalysisResult:
        if not video_path.is_file():
            raise ClipperError(f"Input file not found: {video_path}")
        logger.info("Transcribing %s", video_path.name)
        transcript = self._transcriber.transcribe(video_path)
        logger.info("Detecting clips")
        candidates = self._detector.detect(transcript)
        return AnalysisResult(
            source=video_path.name,
            transcript=transcript,
            candidates=tuple(candidates),
        )
