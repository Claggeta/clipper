import logging
import shutil
import subprocess
from pathlib import Path

from clipper.domain.errors import AudioExtractionError, NoAudioStreamError

logger = logging.getLogger(__name__)

_MAX_ERROR_LINES = 10
# See docs/decisions/0005. A wording change falls through to the generic error.
_NO_AUDIO_MARKER = "does not contain any stream"


class FfmpegAudioExtractor:
    """Extracts 16 kHz mono WAV audio using the ffmpeg command line tool."""

    def __init__(self, executable: str = "ffmpeg") -> None:
        self._executable = executable

    def extract(self, video_path: Path, output_path: Path) -> Path:
        if not video_path.is_file():
            raise AudioExtractionError(f"Input file not found: {video_path}")
        if shutil.which(self._executable) is None:
            raise AudioExtractionError(
                f"'{self._executable}' not found on PATH. Install ffmpeg first."
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            self._executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_path),
            "-map",
            "0:a:0?",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(output_path),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            logger.info("ffmpeg stderr:\n%s", result.stderr)
            if _NO_AUDIO_MARKER in result.stderr.lower():
                raise NoAudioStreamError(
                    f"{video_path} has no audio stream. Clipper needs spoken "
                    "audio to find clips; use a file with an audio track."
                )
            lines = [ln for ln in result.stderr.splitlines() if ln.strip()]
            detail = "\n".join(lines[-_MAX_ERROR_LINES:])
            raise AudioExtractionError(
                f"ffmpeg failed (exit {result.returncode}): {detail}"
            )
        return output_path
