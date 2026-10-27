from collections.abc import Iterable
from pathlib import Path
from typing import Any

from clipper.domain.errors import TranscriptionError
from clipper.domain.models import Segment, Transcript

RawSegment = tuple[float, float, str]


def build_transcript(raw_segments: Iterable[RawSegment]) -> Transcript:
    """Clean raw (start, end, text) tuples into a valid Transcript."""
    segments: list[Segment] = []
    previous_end = 0.0
    for start, end, text in raw_segments:
        text = text.strip()
        start = max(start, previous_end)
        if not text or end <= start:
            continue
        segments.append(Segment(start_seconds=start, end_seconds=end, text=text))
        previous_end = end
    return Transcript(segments=tuple(segments))


class FasterWhisperTranscriber:
    """Transcribes audio locally using faster-whisper."""

    def __init__(
        self,
        model_size: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._model: Any = None

    @property
    def name(self) -> str:
        return f"faster-whisper-{self._model_size}"

    def _load_model(self) -> Any:
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=self._compute_type,
            )
        return self._model

    def transcribe(self, media_path: Path) -> Transcript:
        if not media_path.is_file():
            raise TranscriptionError(f"Input file not found: {media_path}")
        try:
            segments, _info = self._load_model().transcribe(
                str(media_path), vad_filter=True
            )
            return build_transcript((s.start, s.end, s.text) for s in segments)
        except TranscriptionError:
            raise
        except Exception as exc:
            raise TranscriptionError(f"Transcription failed: {exc}") from exc
