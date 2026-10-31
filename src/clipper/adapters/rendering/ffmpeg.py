import logging
from pathlib import Path

from clipper.adapters.media._process import format_failure, run_tool
from clipper.domain.errors import ClipRenderError
from clipper.domain.models import ClipCandidate, RenderedClip

logger = logging.getLogger(__name__)

# Quality settings chosen for ADR 0007. Not measured against real footage:
# crf 20 is slightly better than libx264's default of 23 because the source is
# already lossy; the preset is libx264's default.
_CRF = "20"
_PRESET = "medium"
_AUDIO_BITRATE = "128k"


def _seconds(value: float) -> str:
    """Fixed-point milliseconds, never scientific notation."""
    return f"{value:.3f}"


class FfmpegClipRenderer:
    """Renders a clip by re-encoding with ffmpeg. See docs/decisions/0007."""

    def __init__(self, executable: str = "ffmpeg") -> None:
        self._executable = executable

    def render(
        self, video_path: Path, candidate: ClipCandidate, output_path: Path
    ) -> RenderedClip:
        if not video_path.is_file():
            raise ClipRenderError(f"Input file not found: {video_path}")
        if output_path.resolve() == video_path.resolve():
            raise ClipRenderError(
                f"Output path is the input file, refusing to overwrite: {video_path}"
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        args = [
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-ss",
            _seconds(candidate.start_seconds),
            "-i",
            str(video_path),
            "-t",
            _seconds(candidate.duration_seconds),
            # Video is required. Audio is the first stream only, the one the
            # transcriber heard, and optional so a silent source still renders.
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            _PRESET,
            "-crf",
            _CRF,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            _AUDIO_BITRATE,
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        result = run_tool(
            self._executable, args, error_type=ClipRenderError, log=logger
        )
        if result.returncode != 0:
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                logger.info("Could not remove partial output %s", output_path)
            raise ClipRenderError(format_failure(self._executable, result))
        return RenderedClip(candidate=candidate, path=output_path)
