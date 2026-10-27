from pathlib import Path

from clipper.adapters.transcription.caching import CachingTranscriber
from clipper.adapters.transcription.fake import FakeTranscriber
from clipper.domain.models import Transcript


class CountingTranscriber:
    def __init__(self) -> None:
        self.calls = 0
        self._fake = FakeTranscriber()

    def transcribe(self, media_path: Path) -> Transcript:
        self.calls += 1
        return self._fake.transcribe(media_path)


def _media(tmp_path: Path) -> Path:
    path = tmp_path / "v.wav"
    path.write_bytes(b"audio")
    return path


def test_second_call_uses_cache(tmp_path: Path) -> None:
    inner = CountingTranscriber()
    cached = CachingTranscriber(inner, tmp_path / "cache", "m1")
    media = _media(tmp_path)

    first = cached.transcribe(media)
    second = cached.transcribe(media)

    assert inner.calls == 1
    assert first == second


def test_different_namespace_misses_cache(tmp_path: Path) -> None:
    inner = CountingTranscriber()
    media = _media(tmp_path)
    CachingTranscriber(inner, tmp_path / "cache", "m1").transcribe(media)
    CachingTranscriber(inner, tmp_path / "cache", "m2").transcribe(media)
    assert inner.calls == 2


def test_corrupt_cache_is_recomputed(tmp_path: Path) -> None:
    inner = CountingTranscriber()
    cache_dir = tmp_path / "cache"
    cached = CachingTranscriber(inner, cache_dir, "m1")
    media = _media(tmp_path)
    cached.transcribe(media)
    for file in cache_dir.glob("*.json"):
        file.write_text("not json", encoding="utf-8")

    cached.transcribe(media)

    assert inner.calls == 2
