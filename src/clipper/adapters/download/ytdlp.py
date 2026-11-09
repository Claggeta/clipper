import json
import logging
import shutil
import subprocess
from pathlib import Path

from pydantic import ValidationError

from clipper.adapters.media._process import format_failure
from clipper.core.labeling import clip_url
from clipper.core.sources import video_path, video_url
from clipper.domain.errors import DownloadError
from clipper.domain.models import ClipInfo, VideoSource

logger = logging.getLogger(__name__)

# The vertical frame is 1080 wide, so more than 1080p only costs disk.
_FORMAT_SORT = "res:1080,ext:mp4:m4a"


def _require(executable: str) -> None:
    if shutil.which(executable) is None:
        raise DownloadError(
            f"'{executable}' not found on PATH. Install it with "
            "`uv tool install yt-dlp`, then run `uv tool update-shell` and "
            "open a new terminal."
        )


class YtDlpDownloader:
    """Downloads one video with the yt-dlp program. See docs/decisions/0020.

    yt-dlp runs as a subprocess, not as a library. Its progress goes straight
    to the console; its stderr is captured for the failure message. The user's
    yt-dlp config is ignored, so no cookies or logins are used, and a video
    that is still live is skipped.
    """

    def __init__(self, executable: str = "yt-dlp") -> None:
        self._executable = executable

    def download(self, source: VideoSource, folder: Path) -> Path:
        _require(self._executable)
        folder.mkdir(parents=True, exist_ok=True)
        target = video_path(source, folder)
        args = [
            "--ignore-config",
            "--no-playlist",
            "--match-filter",
            "!is_live",
            "-S",
            _FORMAT_SORT,
            "--merge-output-format",
            "mp4",
            "--remux-video",
            "mp4",
            "-P",
            str(folder),
            "-o",
            f"{source.video_id}.%(ext)s",
            video_url(source),
        ]
        result = subprocess.run(
            [self._executable, *args],
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            logger.info("%s stderr:\n%s", self._executable, result.stderr)
            raise DownloadError(format_failure(self._executable, result))
        if not target.is_file():
            raise DownloadError(
                f"{self._executable} exited 0 but wrote no file at {target}. "
                "A video that is still live is skipped."
            )
        return target


class YtDlpClipLookup:
    """Reads a Twitch clip's metadata with the yt-dlp program.

    Nothing is downloaded (`--skip-download`), and the user's yt-dlp config is
    ignored, so no cookies or logins are used (docs/decisions/0020, clip
    lookups). yt-dlp's JSON goes to stdout; stderr is kept for the failure
    message.
    """

    def __init__(self, executable: str = "yt-dlp") -> None:
        self._executable = executable

    def lookup(self, clip_id: str) -> ClipInfo:
        _require(self._executable)
        args = [
            "--ignore-config",
            "--skip-download",
            "--dump-single-json",
            clip_url(clip_id),
        ]
        result = subprocess.run(
            [self._executable, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            logger.info("%s stderr:\n%s", self._executable, result.stderr)
            raise DownloadError(format_failure(self._executable, result))
        try:
            data = json.loads(result.stdout)
            return ClipInfo(title=data["title"], duration_seconds=data["duration"])
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            raise DownloadError(
                f"{self._executable} gave no usable title and duration for clip "
                f"{clip_id}: {exc}"
            ) from exc
