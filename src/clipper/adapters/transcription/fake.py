from pathlib import Path

from clipper.domain.models import Segment, Transcript


class FakeTranscriber:
    """A Transcriber that returns fixed data, for tests and offline runs."""

    def __init__(self, transcript: Transcript | None = None) -> None:
        self._transcript = transcript or _SAMPLE_TRANSCRIPT

    def transcribe(self, media_path: Path) -> Transcript:
        return self._transcript


_SAMPLE_TRANSCRIPT = Transcript(
    segments=(
        Segment(start_seconds=0.0, end_seconds=4.5, text="Welcome back to the show."),
        Segment(
            start_seconds=4.5,
            end_seconds=9.0,
            text="So why did you decide to leave your job?",
        ),
        Segment(
            start_seconds=9.0,
            end_seconds=13.5,
            text="Honestly, it came down to one thing.",
        ),
    )
)
