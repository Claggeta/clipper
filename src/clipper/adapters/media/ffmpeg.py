import logging
from pathlib import Path

from clipper.adapters.media._process import format_failure, run_tool
from clipper.domain.errors import AudioExtractionError, NoAudioStreamError

logger = logging.getLogger(__name__)

# See docs/decisions/0005. A wording change falls through to the generic error.
_NO_AUDIO_MARKER = "does not contain any stream"


class FfmpegAudioExtractor:
    """Extracts 16 kHz mono WAV audio using the ffmpeg command line tool."""

    def __init__(self, executable: str = "ffmpeg") -> None:
        self._executable = executable

    def extract(self, video_path: Path, output_path: Path) -> Path:
        if not video_path.is_file():
            raise AudioExtractionError(f"Input file not found: {video_path}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        args = [
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
        result = run_tool(
            self._executable, args, error_type=AudioExtractionError, log=logger
        )
        if result.returncode != 0:
            if _NO_AUDIO_MARKER in result.stderr.lower():
                raise NoAudioStreamError(
                    f"{video_path} has no audio stream. Clipper needs spoken "
                    "audio to find clips; use a file with an audio track."
                )
            raise AudioExtractionError(format_failure(self._executable, result))
        return output_path
