from pathlib import Path

from clipper.adapters.transcription.fake import FakeTranscriber
from clipper.core.interfaces import Transcriber


def test_fake_transcriber_satisfies_protocol() -> None:
    transcriber: Transcriber = FakeTranscriber()
    assert isinstance(transcriber, Transcriber)


def test_fake_transcriber_returns_transcript() -> None:
    transcript = FakeTranscriber().transcribe(Path("anything.mp4"))
    assert transcript.full_text
    assert transcript.duration_seconds > 0


def test_fake_transcriber_can_accept_custom_transcript() -> None:
    custom = FakeTranscriber(transcript=None)
    assert custom.transcribe(Path("x.mp4")).segments


def test_fake_transcriber_reflects_media_path_independence() -> None:
    transcriber = FakeTranscriber()
    assert transcriber.transcribe(Path("a.mp4")) == transcriber.transcribe(
        Path("b.mp4")
    )
