from pathlib import Path
from typing import Protocol, runtime_checkable

from clipper.domain.models import Transcript


@runtime_checkable
class Transcriber(Protocol):
    """Turns a video or audio file into a Transcript."""

    def transcribe(self, media_path: Path) -> Transcript: ...


@runtime_checkable
class AudioExtractor(Protocol):
    """Extracts an audio track from a video file into a WAV file."""

    def extract(self, video_path: Path, output_path: Path) -> Path: ...
