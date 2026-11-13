import logging
import sys
from array import array
from pathlib import Path

from clipper.adapters.media._process import format_failure, run_tool
from clipper.domain.errors import PeaksError

logger = logging.getLogger(__name__)

# Levels per second of video: 50 ms each, fine enough to trim on at the
# strip's closest zoom (0.25 s a frame), about 360 KB for five hours.
PEAKS_PER_SECOND = 20
# The audio is decoded at this rate for the levels alone; loudness, not
# sound, so a low rate keeps the temporary file small (72 MB for 5 hours).
SAMPLE_RATE = 2000
_SAMPLES_PER_PEAK = SAMPLE_RATE // PEAKS_PER_SECOND
_FULL_SCALE = 32768
# Samples read from the temporary file at a time.
_CHUNK_SAMPLES = _SAMPLES_PER_PEAK * 4096


def peaks_from_pcm(pcm_path: Path) -> bytes:
    """The loudest sample in each `1 / PEAKS_PER_SECOND` s of a file of
    mono signed 16-bit little-endian samples, scaled to 0 to 255. A short
    last stretch counts as a whole one."""
    levels = bytearray()
    with pcm_path.open("rb") as source:
        while chunk := source.read(_CHUNK_SAMPLES * 2):
            samples = array("h")
            samples.frombytes(chunk[: len(chunk) - len(chunk) % 2])
            if sys.byteorder == "big":
                samples.byteswap()
            for at in range(0, len(samples), _SAMPLES_PER_PEAK):
                part = samples[at : at + _SAMPLES_PER_PEAK]
                peak = max(max(part), -min(part))
                levels.append(min(peak * 256 // _FULL_SCALE, 255))
    return bytes(levels)


class FfmpegPeaksExtractor:
    """Writes a video's audio levels using the ffmpeg command line tool.

    ffmpeg decodes the first audio stream to a temporary file of raw
    samples beside the output, which is read in chunks and deleted. See
    docs/decisions/0031 (precision amendment).
    """

    def __init__(self, executable: str = "ffmpeg") -> None:
        self._executable = executable

    def extract(self, video_path: Path, output_path: Path) -> Path:
        if not video_path.is_file():
            raise PeaksError(f"Input file not found: {video_path}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        pcm = output_path.with_name(f"{output_path.name}.pcm")
        args = [
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_path),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "s16le",
            str(pcm),
        ]
        try:
            result = run_tool(self._executable, args, error_type=PeaksError, log=logger)
            if result.returncode != 0:
                raise PeaksError(format_failure(self._executable, result))
            output_path.write_bytes(peaks_from_pcm(pcm))
        finally:
            pcm.unlink(missing_ok=True)
        return output_path
