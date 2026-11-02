from collections.abc import Iterable
from pathlib import Path
from typing import Any

from clipper.domain.errors import TranscriptionError
from clipper.domain.models import Segment, Transcript, Word

RawWord = tuple[float, float, str]
RawSegment = tuple[float, float, str, Iterable[RawWord]]


def _clean_words(
    raw_words: Iterable[RawWord], start: float, end: float
) -> tuple[Word, ...]:
    """Clamp words into [start, end] in order. Empty words are dropped; a word
    left with no time keeps zero length rather than losing its text.

    faster-whisper starts each real word with a space. A token without one
    continues the previous word: its default prepend punctuation splits
    "battle-hardened" into " battle" and "-hardened". Such a token is merged
    back into the word before it.
    """
    words: list[Word] = []
    previous_end = start
    for word_start, word_end, raw_text in raw_words:
        text = raw_text.strip()
        if not text:
            continue
        word_start = min(max(word_start, previous_end), end)
        word_end = min(max(word_end, word_start), end)
        if words and not raw_text[:1].isspace():
            last = words.pop()
            word_start, text = last.start_seconds, last.text + text
        words.append(Word(start_seconds=word_start, end_seconds=word_end, text=text))
        previous_end = word_end
    return tuple(words)


def build_transcript(raw_segments: Iterable[RawSegment]) -> Transcript:
    """Clean raw (start, end, text, words) tuples into a valid Transcript."""
    segments: list[Segment] = []
    previous_end = 0.0
    for start, end, text, raw_words in raw_segments:
        text = text.strip()
        start = max(start, previous_end)
        if not text or end <= start:
            continue
        segments.append(
            Segment(
                start_seconds=start,
                end_seconds=end,
                text=text,
                words=_clean_words(raw_words, start, end),
            )
        )
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
        # "+words" keys the transcript cache apart from transcripts made
        # without word timestamps, which are re-transcribed (ADR 0017).
        return f"faster-whisper-{self._model_size}+words"

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
                str(media_path), vad_filter=True, word_timestamps=True
            )
            return build_transcript(
                (
                    s.start,
                    s.end,
                    s.text,
                    [(w.start, w.end, w.word) for w in s.words or ()],
                )
                for s in segments
            )
        except TranscriptionError:
            raise
        except Exception as exc:
            raise TranscriptionError(f"Transcription failed: {exc}") from exc
