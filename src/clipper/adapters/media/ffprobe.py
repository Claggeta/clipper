import json
import logging
from pathlib import Path

from pydantic import ValidationError

from clipper.adapters.media._process import format_failure, run_tool
from clipper.domain.errors import MediaProbeError
from clipper.domain.models import MediaInfo

logger = logging.getLogger(__name__)


class FfprobeMediaProbe:
    """Reads duration, start time, video presence and frame size using the
    ffprobe command line tool.

    Reports structurally from ffprobe's JSON output and never matches stderr
    text. See docs/decisions/0009.
    """

    def __init__(self, executable: str = "ffprobe") -> None:
        self._executable = executable

    def probe(self, media_path: Path) -> MediaInfo:
        if not media_path.is_file():
            raise MediaProbeError(f"Input file not found: {media_path}")

        args = [
            "-v",
            "error",
            "-show_entries",
            "format=duration,start_time:stream=codec_type,width,height"
            ":stream_disposition=attached_pic",
            "-of",
            "json",
            str(media_path),
        ]
        result = run_tool(
            self._executable, args, error_type=MediaProbeError, log=logger
        )
        # A failed run still prints a bare "{" on stdout, so check the exit
        # code before parsing.
        if result.returncode != 0:
            raise MediaProbeError(format_failure(self._executable, result))

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise MediaProbeError(
                f"{self._executable} returned invalid JSON for {media_path}: {exc}"
            ) from exc
        return self._parse(data, media_path)

    def _parse(self, data: object, media_path: Path) -> MediaInfo:
        if not isinstance(data, dict):
            raise MediaProbeError(f"Unexpected ffprobe output for {media_path}")
        fmt = data.get("format")
        streams = data.get("streams", [])
        if not isinstance(fmt, dict) or not isinstance(streams, list):
            raise MediaProbeError(f"Unexpected ffprobe output for {media_path}")

        raw_duration = fmt.get("duration")
        video = next((s for s in streams if _is_video(s)), None)
        width, height = _size(video)
        try:
            duration = float(raw_duration)  # type: ignore[arg-type]
            info = MediaInfo(
                duration_seconds=duration,
                has_video=video is not None,
                width=width,
                height=height,
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise MediaProbeError(
                f"No usable duration for {media_path} (got {raw_duration!r})"
            ) from exc
        # Absent when ffprobe cannot tell; then the file is taken to start at 0.
        raw_start = fmt.get("start_time")
        if raw_start is None:
            return info
        try:
            # Built again, not copied, so the start time is validated too.
            return MediaInfo(**{**info.model_dump(), "start_seconds": float(raw_start)})
        except (TypeError, ValueError, ValidationError) as exc:
            raise MediaProbeError(
                f"No usable start time for {media_path} (got {raw_start!r})"
            ) from exc


def _size(stream: object) -> tuple[int | None, int | None]:
    """A video stream's width and height, or None for a missing or bad value."""
    if not isinstance(stream, dict):
        return None, None
    sizes = [stream.get(key) for key in ("width", "height")]
    if all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in sizes):
        return sizes[0], sizes[1]
    return None, None


def _is_video(stream: object) -> bool:
    if not isinstance(stream, dict) or stream.get("codec_type") != "video":
        return False
    disposition = stream.get("disposition")
    attached = disposition.get("attached_pic") if isinstance(disposition, dict) else 0
    return attached != 1
