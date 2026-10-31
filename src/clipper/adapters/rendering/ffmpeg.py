import logging
import tempfile
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

# ADR 0010. The filter reads a bare filename from ffmpeg's working directory, so
# no path ever reaches the filter's escaping rules (`:` and `\` on Windows).
# The style is one fixed default; FontSize is relative to libass's 288-pixel
# canvas for SRT, not pixels. Values are a first guess, checked by eye only.
_SRT_NAME = "clip.srt"
_SUBTITLES_FILTER = (
    f"subtitles={_SRT_NAME}:charenc=UTF-8:"
    "force_style='FontSize=18,Outline=2,Shadow=0,MarginV=30'"
)


def _seconds(value: float) -> str:
    """Fixed-point milliseconds, never scientific notation."""
    return f"{value:.3f}"


class FfmpegClipRenderer:
    """Renders a clip by re-encoding with ffmpeg. See docs/decisions/0007."""

    def __init__(self, executable: str = "ffmpeg") -> None:
        self._executable = executable

    def render(
        self,
        video_path: Path,
        candidate: ClipCandidate,
        output_path: Path,
        captions: str | None = None,
    ) -> RenderedClip:
        if not video_path.is_file():
            raise ClipRenderError(f"Input file not found: {video_path}")
        if output_path.resolve() == video_path.resolve():
            raise ClipRenderError(
                f"Output path is the input file, refusing to overwrite: {video_path}"
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not captions:
            self._cut(video_path, candidate, output_path)
        else:
            with tempfile.TemporaryDirectory(
                prefix="clipper-captions-", ignore_cleanup_errors=True
            ) as folder:
                # UTF-8 with no BOM and LF line endings, whatever the platform.
                (Path(folder) / _SRT_NAME).write_bytes(captions.encode("utf-8"))
                # ffmpeg runs in `folder`, so the other paths must not depend on
                # the caller's working directory.
                self._cut(
                    video_path.resolve(),
                    candidate,
                    output_path.resolve(),
                    cwd=Path(folder),
                    video_filter=_SUBTITLES_FILTER,
                )
        return RenderedClip(candidate=candidate, path=output_path)

    def _cut(
        self,
        video_path: Path,
        candidate: ClipCandidate,
        output_path: Path,
        *,
        cwd: Path | None = None,
        video_filter: str | None = None,
    ) -> None:
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
            *(["-vf", video_filter] if video_filter else []),
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
            self._executable, args, error_type=ClipRenderError, log=logger, cwd=cwd
        )
        if result.returncode != 0:
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                logger.info("Could not remove partial output %s", output_path)
            raise ClipRenderError(format_failure(self._executable, result))
