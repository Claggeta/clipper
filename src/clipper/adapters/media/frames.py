import logging
from pathlib import Path

from clipper.adapters.media._process import format_failure, run_tool
from clipper.domain.errors import FrameError

logger = logging.getLogger(__name__)

# Thumbnail height in pixels; the width follows the video's shape.
FRAME_HEIGHT = 72


class FfmpegFrameExtractor:
    """Saves one small JPEG frame using the ffmpeg command line tool.

    `-ss` before `-i` seeks in the input instead of decoding up to the time.
    See docs/decisions/0024 (film strip amendment).
    """

    def __init__(self, executable: str = "ffmpeg") -> None:
        self._executable = executable

    def extract(self, video_path: Path, seconds: float, output_path: Path) -> Path:
        if not video_path.is_file():
            raise FrameError(f"Input file not found: {video_path}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        args = [
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{seconds:.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-vf",
            f"scale=-2:{FRAME_HEIGHT}",
            "-q:v",
            "5",
            str(output_path),
        ]
        result = run_tool(self._executable, args, error_type=FrameError, log=logger)
        if result.returncode != 0:
            raise FrameError(format_failure(self._executable, result))
        # A guard, not an observed case: a time past the end exited non-zero
        # ("Nothing was written into output file") on the one VOD tried.
        if not output_path.is_file():
            raise FrameError(f"No frame at {seconds:.3f} s in {video_path}")
        return output_path
