from pathlib import Path
from typing import Protocol, runtime_checkable

from clipper.domain.models import Transcript


@runtime_checkable
class Transcriber(Protocol):
    """Turns a video or audio file into a Transcript."""

    def transcribe(self, media_path: Path) -> Transcript: ...
