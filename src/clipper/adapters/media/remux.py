import logging
import os
from pathlib import Path

from clipper.adapters.media._process import format_failure, run_tool
from clipper.domain.errors import RemuxError

logger = logging.getLogger(__name__)


class FfmpegRemuxer:
    """Rewrites a video so its timestamps start at 0, without re-encoding.

    See docs/decisions/0020 (remux amendment). ffmpeg subtracts the input's
    start time unless told to keep it (`-copyts`), so a stream copy starts at
    0; `+faststart` puts the index first, so a browser can seek at once. The
    copy goes to `<stem>.part<suffix>` beside the video and replaces it only
    when ffmpeg succeeds.
    """

    def __init__(self, executable: str = "ffmpeg") -> None:
        self._executable = executable

    def remux(self, video_path: Path) -> None:
        temp = video_path.with_name(f"{video_path.stem}.part{video_path.suffix}")
        args = [
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(video_path),
            "-map",
            "0",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(temp),
        ]
        result = run_tool(self._executable, args, error_type=RemuxError, log=logger)
        if result.returncode != 0:
            temp.unlink(missing_ok=True)
            raise RemuxError(format_failure(self._executable, result))
        try:
            os.replace(temp, video_path)
        except OSError as exc:
            temp.unlink(missing_ok=True)
            raise RemuxError(
                f"Cannot replace {video_path} with its remux: {exc}"
            ) from exc
