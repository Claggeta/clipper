import hashlib
import logging
from pathlib import Path

from pydantic import ValidationError

from clipper.core.interfaces import Transcriber
from clipper.domain.models import Transcript

logger = logging.getLogger(__name__)


class CachingTranscriber:
    """Wraps a Transcriber and stores results as JSON files on disk."""

    def __init__(self, inner: Transcriber, cache_dir: Path, namespace: str) -> None:
        self._inner = inner
        self._cache_dir = cache_dir
        self._namespace = namespace

    def _cache_path(self, media_path: Path) -> Path:
        stat = media_path.stat()
        key = f"{self._namespace}|{media_path.name}|{stat.st_size}|{stat.st_mtime_ns}"
        digest = hashlib.sha256(key.encode()).hexdigest()[:24]
        return self._cache_dir / f"{digest}.json"

    def transcribe(self, media_path: Path) -> Transcript:
        cache_file = self._cache_path(media_path)
        if cache_file.is_file():
            try:
                transcript = Transcript.model_validate_json(
                    cache_file.read_text(encoding="utf-8")
                )
                logger.info("Transcript cache hit: %s", cache_file.name)
                return transcript
            except (ValidationError, OSError):
                logger.warning("Ignoring unreadable cache file %s", cache_file)

        transcript = self._inner.transcribe(media_path)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(transcript.model_dump_json(), encoding="utf-8")
        return transcript
