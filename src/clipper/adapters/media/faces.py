import logging
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
from pydantic import ValidationError

from clipper.adapters.media._process import format_failure
from clipper.core.interfaces import FaceTracker
from clipper.domain.errors import FaceError
from clipper.domain.models import FaceBox, FaceSample, FaceTrack

logger = logging.getLogger(__name__)

# The spike's settings (ADR 0025): 5 samples a second at 1280x720, faces
# scoring at least 0.8.
SAMPLES_PER_SECOND = 5.0
FRAME_WIDTH = 1280
FRAME_HEIGHT = 720
MIN_SCORE = 0.8
# YuNet, MIT licence (LICENSE-yunet beside it), from opencv/opencv_zoo.
MODEL_PATH = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"
# Names the model and settings in the cache, so a change re-tracks.
TRACKER_NAME = "yunet2023mar-5fps-1280x720"

_FRAME_BYTES = FRAME_WIDTH * FRAME_HEIGHT * 3
# YuNet's row: box (4 values), 5 landmarks (10 values), then the score.
_SCORE_COLUMN = 14


class OpenCvFaceTracker:
    """Finds faces with OpenCV's YuNet model on frames piped from ffmpeg.

    ffmpeg decodes the span, samples it and scales every frame to 1280x720,
    which the 2023mar model's fixed input size needs. A source that is not
    16:9 is squashed to fit; boxes are still fractions of its frame. See
    docs/decisions/0025.
    """

    def __init__(self, executable: str = "ffmpeg", model: Path = MODEL_PATH) -> None:
        self._executable = executable
        self._model = model

    def track(
        self, video_path: Path, start_seconds: float, end_seconds: float
    ) -> FaceTrack:
        if not video_path.is_file():
            raise FaceError(f"Input file not found: {video_path}")
        if shutil.which(self._executable) is None:
            raise FaceError(
                f"'{self._executable}' not found on PATH. Install ffmpeg first."
            )
        detector = self._detector()
        args = [
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start_seconds:.3f}",
            "-t",
            f"{end_seconds - start_seconds:.3f}",
            "-i",
            str(video_path),
            "-vf",
            f"fps={SAMPLES_PER_SECOND:g},scale={FRAME_WIDTH}:{FRAME_HEIGHT}",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-",
        ]
        # Frames are read as they come, not held: a minute is 300 frames of
        # 2.7 MB. stderr stays small with `-loglevel error`, so it cannot fill
        # its pipe while stdout is read.
        process = subprocess.Popen(
            [self._executable, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        assert process.stdout is not None
        samples: list[FaceSample] = []
        while len(chunk := process.stdout.read(_FRAME_BYTES)) == _FRAME_BYTES:
            frame = np.frombuffer(chunk, np.uint8).reshape(FRAME_HEIGHT, FRAME_WIDTH, 3)
            _, found = detector.detect(frame)
            seconds = start_seconds + len(samples) / SAMPLES_PER_SECOND
            samples.append(FaceSample(seconds=seconds, faces=_boxes(found)))
        _, stderr = process.communicate()
        if process.returncode != 0:
            text = stderr.decode("utf-8", errors="replace")
            logger.info("%s stderr:\n%s", self._executable, text)
            result = subprocess.CompletedProcess(args, process.returncode, "", text)
            raise FaceError(format_failure(self._executable, result))
        return FaceTrack(
            source=video_path.name,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            samples_per_second=SAMPLES_PER_SECOND,
            samples=tuple(samples),
        )

    def _detector(self) -> cv2.FaceDetectorYN:
        if not self._model.is_file():
            raise FaceError(f"Face model not found: {self._model}")
        # OpenCV 5 warns on every load that its new engine ignores targets;
        # only errors are worth showing.
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
        try:
            return cv2.FaceDetectorYN.create(
                str(self._model),
                "",
                (FRAME_WIDTH, FRAME_HEIGHT),
                score_threshold=MIN_SCORE,
            )
        except cv2.error as exc:
            raise FaceError(f"Cannot load face model {self._model}: {exc}") from exc


def _boxes(found: object) -> tuple[FaceBox, ...]:
    """YuNet's rows as boxes in fractions of the frame; None means no face."""
    if found is None:
        return ()
    boxes = []
    for row in np.asarray(found, dtype=float):
        x, y, w, h = row[:4]
        if w <= 0 or h <= 0:
            continue
        boxes.append(
            FaceBox(
                x=x / FRAME_WIDTH,
                y=y / FRAME_HEIGHT,
                w=w / FRAME_WIDTH,
                h=h / FRAME_HEIGHT,
                score=min(float(row[_SCORE_COLUMN]), 1.0),
            )
        )
    return tuple(boxes)


class CachingFaceTracker:
    """Wraps a FaceTracker and keeps each span's faces as JSON on disk.

    One file per span, `<cache dir>/<video stem>-<size>/<name>-<start ms>-<end
    ms>.json`, so a re-render or a second style reuses it (ADR 0025).
    """

    def __init__(self, inner: FaceTracker, cache_dir: Path, name: str) -> None:
        self._inner = inner
        self._cache_dir = cache_dir
        self._name = name

    def _cache_path(self, video_path: Path, start: float, end: float) -> Path:
        folder = f"{video_path.stem}-{video_path.stat().st_size}"
        span = f"{round(start * 1000)}-{round(end * 1000)}"
        return self._cache_dir / folder / f"{self._name}-{span}.json"

    def track(
        self, video_path: Path, start_seconds: float, end_seconds: float
    ) -> FaceTrack:
        if not video_path.is_file():
            raise FaceError(f"Input file not found: {video_path}")
        cache_file = self._cache_path(video_path, start_seconds, end_seconds)
        if cache_file.is_file():
            try:
                track = FaceTrack.model_validate_json(cache_file.read_bytes())
                logger.info("Face cache hit: %s", cache_file.name)
                return track
            except (ValidationError, OSError):
                logger.warning("Ignoring unreadable cache file %s", cache_file)

        track = self._inner.track(video_path, start_seconds, end_seconds)
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(track.model_dump_json(), encoding="utf-8")
        except OSError as exc:
            logger.warning("Cannot write face cache %s: %s", cache_file, exc)
        return track
