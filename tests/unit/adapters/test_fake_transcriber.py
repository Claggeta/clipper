from pathlib import Path

from clipper.adapters.transcription.fake import FakeTranscriber
from clipper.core.interfaces import Transcriber
from clipper.domain.models import Segment, Transcript


def test_fake_transcriber_satisfies_protocol() -> None:
    transcriber: Transcriber = FakeTranscriber()
    assert isinstance(transcriber, Transcriber)


def test_fake_transcriber_returns_transcript() -> None:
    transcript = FakeTranscriber().transcribe(Path("anything.mp4"))
    assert transcript.full_text
    assert transcript.duration_seconds > 0


def test_fake_transcriber_can_accept_custom_transcript() -> None:
    custom = Transcript(
        segments=(Segment(start_seconds=0.0, end_seconds=2.0, text="custom"),)
    )
    transcriber = FakeTranscriber(transcript=custom)
    assert transcriber.transcribe(Path("x.mp4")).full_text == "custom"


def test_fake_transcriber_ignores_media_path() -> None:
    transcriber = FakeTranscriber()
    assert transcriber.transcribe(Path("a.mp4")) == transcriber.transcribe(
        Path("b.mp4")
    )
