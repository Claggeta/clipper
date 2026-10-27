import tempfile
from pathlib import Path

from clipper.core.interfaces import AudioExtractor, Transcriber
from clipper.domain.models import Transcript


class VideoTranscriber:
    """Accepts a video path; extracts audio, then transcribes it."""

    def __init__(self, extractor: AudioExtractor, inner: Transcriber) -> None:
        self._extractor = extractor
        self._inner = inner

    def transcribe(self, media_path: Path) -> Transcript:
        with tempfile.TemporaryDirectory(prefix="clipper-") as tmp:
            audio = self._extractor.extract(media_path, Path(tmp) / "audio.wav")
            return self._inner.transcribe(audio)
