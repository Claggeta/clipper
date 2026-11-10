import logging
import tempfile
from pathlib import Path

from clipper.adapters.media._process import format_failure, run_tool
from clipper.domain.errors import ClipRenderError
from clipper.domain.models import ClipCandidate, CropPath, RenderedClip

logger = logging.getLogger(__name__)

# Quality settings chosen for ADR 0007. Not measured against real footage:
# crf 20 is slightly better than libx264's default of 23 because the source is
# already lossy; the preset is libx264's default.
_CRF = "20"
_PRESET = "medium"
_AUDIO_BITRATE = "128k"

# ADR 0010. The filter reads a bare filename from ffmpeg's working directory, so
# no path ever reaches the filter's escaping rules (`:` and `\` on Windows).
# The captions are ASS and carry their own style (ADR 0017), so there is no
# force_style.
_CAPTIONS_NAME = "clip.ass"
_SUBTITLES_FILTER = f"subtitles={_CAPTIONS_NAME}:charenc=UTF-8"

# ADR 0016. The whole frame, fitted inside 1080x1920 at even sizes, centred on
# a blurred copy of itself scaled to fill the frame. The blur runs at quarter
# size (270x480) and is scaled back up, which is far cheaper than blurring at
# full size. Blur strength is a first guess, checked by eye only. setsar=1
# stops players stretching the result.
_VERTICAL_FILTER = (
    "split[bg][fg];"
    "[bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
    "boxblur=10:2,scale=1080:1920[blurred];"
    "[fg]scale=1080:1920:force_original_aspect_ratio=decrease:"
    "force_divisible_by=2[fitted];"
    "[blurred][fitted]overlay=(W-w)/2:(H-h)/2,setsar=1"
)

# ADR 0025. The face style's moving crop: `sendcmd` reads the window's left
# edge over time from a bare filename in ffmpeg's working directory, as the
# captions do, and moves the named crop; then the window is scaled to
# 1080x1920.
_CROP_NAME = "crop.cmd"


def _face_filter(crop: CropPath) -> str:
    left = crop.points[0][1]
    return (
        f"sendcmd=f={_CROP_NAME},crop@face={crop.width}:{crop.height}:{left}:0,"
        "scale=1080:1920,setsar=1"
    )


def _crop_commands(crop: CropPath) -> str:
    """One `sendcmd` line per point: from that time, the crop's left edge."""
    return "".join(f"{_seconds(t)} crop@face x {left};\n" for t, left in crop.points)


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
        *,
        vertical: bool = False,
        crop: CropPath | None = None,
    ) -> RenderedClip:
        if not video_path.is_file():
            raise ClipRenderError(f"Input file not found: {video_path}")
        if output_path.resolve() == video_path.resolve():
            raise ClipRenderError(
                f"Output path is the input file, refusing to overwrite: {video_path}"
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Make the vertical frame first, so captions are laid out on it. A
        # crop path is the face style, in place of the fit (ADR 0025).
        filters: list[str] = []
        # UTF-8 with no BOM and LF line endings, whatever the platform.
        files: dict[str, bytes] = {}
        if crop is not None:
            filters.append(_face_filter(crop))
            files[_CROP_NAME] = _crop_commands(crop).encode("utf-8")
        elif vertical:
            filters.append(_VERTICAL_FILTER)
        if captions:
            filters.append(_SUBTITLES_FILTER)
            files[_CAPTIONS_NAME] = captions.encode("utf-8")
        video_filter = ",".join(filters) or None
        if not files:
            self._cut(video_path, candidate, output_path, video_filter=video_filter)
        else:
            with tempfile.TemporaryDirectory(
                prefix="clipper-render-", ignore_cleanup_errors=True
            ) as folder:
                for name, data in files.items():
                    (Path(folder) / name).write_bytes(data)
                # ffmpeg runs in `folder`, so the other paths must not depend on
                # the caller's working directory.
                self._cut(
                    video_path.resolve(),
                    candidate,
                    output_path.resolve(),
                    cwd=Path(folder),
                    video_filter=video_filter,
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
