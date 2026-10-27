import shutil
import subprocess
from pathlib import Path

from clipper.domain.errors import AudioExtractionError


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
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(output_path),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise AudioExtractionError(
                f"ffmpeg failed (exit {result.returncode}): {result.stderr[-500:]}"
            )
        return output_path
