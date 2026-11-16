import logging
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ValidationError

from clipper.adapters.media._process import format_failure
from clipper.adapters.media.faces import (
    FRAME_HEIGHT,
    FRAME_WIDTH,
    MODEL_PATH,
    load_face_model,
)
from clipper.core.interfaces import FacecamFinder
from clipper.domain.errors import FaceError
from clipper.domain.models import FacecamBox

logger = logging.getLogger(__name__)

# The facecam spike's settings (ADR 0025, facecam spike amendment): frames at
# 2 a second, 1280x720. A face that stays put has its centre within STILL of
# the frame's width in at least MIN_SHARE of the frames. A border is searched
# out to REACH face sizes; it stands out at STANDS_OUT times the profile's
# median and holds at least half its strength along SOLID of the side. A box
# is at least MIN_SIDE of the frame each way, at most MAX_AREA of it, never
# spans it, and has at least three real borders.
SAMPLES_PER_SECOND = 2.0
STILL = 0.04
MIN_SHARE = 0.5
REACH = 6.0
STANDS_OUT = 2.5
SOLID = 0.8
MIN_SIDE = 0.15
MAX_AREA = 0.5
SPANS = 0.95
# Names the model and settings in the cache, so a change finds boxes again.
FINDER_NAME = "facecam1-yunet2023mar-2fps-1280x720"

_FRAME_BYTES = FRAME_WIDTH * FRAME_HEIGHT * 3
# Faces as rows of frame index, x, y, w, h, in pixels at 1280x720.
Faces = NDArray[np.float64]
Grid = NDArray[np.float32]


class OpenCvFacecamFinder:
    """Finds a fixed facecam box from faces and lasting straight edges.

    ffmpeg decodes the span at 2 frames a second, scaled to 1280x720; YuNet
    finds the faces and each frame's edges are averaged over the span. See
    docs/decisions/0025 (facecam spike and facecam style amendments).
    """

    def __init__(self, executable: str = "ffmpeg", model: Path = MODEL_PATH) -> None:
        self._executable = executable
        self._model = model

    def find(
        self, video_path: Path, start_seconds: float, end_seconds: float
    ) -> FacecamBox | None:
        if not video_path.is_file():
            raise FaceError(f"Input file not found: {video_path}")
        if shutil.which(self._executable) is None:
            raise FaceError(
                f"'{self._executable}' not found on PATH. Install ffmpeg first."
            )
        detector = load_face_model(self._model)
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
        # Frames are read as they come and only their edges kept, as the face
        # tracker reads them.
        process = subprocess.Popen(
            [self._executable, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        assert process.stdout is not None
        rows: list[list[float]] = []
        gx = np.zeros((FRAME_HEIGHT, FRAME_WIDTH), np.float64)
        gy = np.zeros((FRAME_HEIGHT, FRAME_WIDTH), np.float64)
        frames = 0
        while len(chunk := process.stdout.read(_FRAME_BYTES)) == _FRAME_BYTES:
            frame = np.frombuffer(chunk, np.uint8).reshape(FRAME_HEIGHT, FRAME_WIDTH, 3)
            _, found = detector.detect(frame)
            if found is not None:
                rows.extend([frames, *row[:4]] for row in np.asarray(found, float))
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
            gx += np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
            gy += np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
            frames += 1
        _, stderr = process.communicate()
        if process.returncode != 0:
            text = stderr.decode("utf-8", errors="replace")
            logger.info("%s stderr:\n%s", self._executable, text)
            result = subprocess.CompletedProcess(args, process.returncode, "", text)
            raise FaceError(format_failure(self._executable, result))
        if frames == 0:
            return None
        faces = np.array(rows, np.float64).reshape(-1, 5)
        return facecam_box(
            faces,
            frames,
            (gx / frames).astype(np.float32),
            (gy / frames).astype(np.float32),
        )


def facecam_box(faces: Faces, frames: int, gx: Grid, gy: Grid) -> FacecamBox | None:
    """The box around the first face that stays put and has one, or None.

    `gx` and `gy` are the mean |Sobel| across and down, frame-sized.
    """
    height, width = gx.shape
    for face in still_faces(faces, frames, width):
        box = box_around(face, gx, gy)
        if usable(box, width, height):
            x0, y0, x1, y1 = box
            return FacecamBox(
                x=x0 / width, y=y0 / height, w=(x1 - x0) / width, h=(y1 - y0) / height
            )
    return None


def still_faces(
    faces: Faces, frames: int, width: int
) -> list[tuple[float, float, float, float]]:
    """The median boxes (x0, y0, x1, y1) of faces that stay put, most frames
    first."""
    left = faces
    found = []
    while len(left):
        centres = np.c_[left[:, 1] + left[:, 3] / 2, left[:, 2] + left[:, 4] / 2]
        best: NDArray[np.bool_] | None = None
        best_frames = 0
        for centre in centres:
            near = np.linalg.norm(centres - centre, axis=1) < STILL * width
            count = len(np.unique(left[near, 0]))
            if count > best_frames:
                best, best_frames = near, count
        if best is None or best_frames < MIN_SHARE * frames:
            break
        _, x, y, w, h = (float(v) for v in np.median(left[best], axis=0))
        found.append((x, y, x + w, y + h))
        left = left[~best]
    return found


def box_around(
    face: tuple[float, float, float, float], gx: Grid, gy: Grid
) -> tuple[int, int, int, int]:
    """From the face outward, the nearest solid border on each side, else the
    frame's edge: first along the face's own extent, which is inside the cam,
    then along the box's sides clear of its corners."""
    height, width = gx.shape
    fx0, fy0, fx1, fy1 = face
    fw, fh = fx1 - fx0, fy1 - fy0
    x0, y0, x1, y1 = int(fx0), int(fy0), int(fx1), int(fy1)
    inset = 0
    across = gy.T  # rows become columns, so one search serves both
    for _ in range(2):
        rows = slice(max(y0 + inset, 0), min(y1 - inset, height))
        cols = slice(max(x0 + inset, 0), min(x1 - inset, width))
        inset = 6
        x0 = _border(gx, rows, fx0 - 0.2 * fw, fx0 - REACH * fw, 0)
        x1 = _border(gx, rows, fx1 + 0.2 * fw, fx1 + REACH * fw, width)
        y0 = _border(across, cols, fy0 - 0.2 * fh, fy0 - REACH * fh, 0)
        y1 = _border(across, cols, fy1 + 0.2 * fh, fy1 + REACH * fh, height)
    return x0, y0, x1, y1


def _border(grad: Grid, along: slice, near: float, far: float, frame_edge: int) -> int:
    """The first solid edge in `grad`'s columns from `near` towards `far`."""
    band = grad[along]
    profile = band.mean(axis=0)
    last = len(profile) - 1
    floor = STANDS_OUT * float(np.median(profile))
    step = 1 if far > near else -1
    start = int(np.clip(near, 0, last))
    stop = int(np.clip(far, 0, last))
    for i in range(start, stop + step, step):
        value = profile[i]
        if value < floor:
            continue
        # A local peak only, so a thick edge counts once.
        if profile[max(i - 1, 0)] > value or profile[min(i + 1, last)] > value:
            continue
        if (band[:, i] > value / 2).mean() >= SOLID:
            return i
    return frame_edge


def usable(box: tuple[int, int, int, int], width: int, height: int) -> bool:
    """A facecam's size, inside the frame, with at least three real borders."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    real = (x0 > 4) + (y0 > 4) + (x1 < width - 4) + (y1 < height - 4)
    return (
        real >= 3
        and w < SPANS * width
        and h < SPANS * height
        and w >= MIN_SIDE * width
        and h >= MIN_SIDE * height
        and w * h <= MAX_AREA * width * height
    )


class _Found(BaseModel):
    """A cached answer, box or none."""

    box: FacecamBox | None


class CachingFacecamFinder:
    """Wraps a FacecamFinder and keeps each span's answer as JSON on disk.

    One file per span, `<cache dir>/<video stem>-<size>/<name>-<start ms>-<end
    ms>.json`, as the face tracker's (ADR 0025).
    """

    def __init__(self, inner: FacecamFinder, cache_dir: Path, name: str) -> None:
        self._inner = inner
        self._cache_dir = cache_dir
        self._name = name

    def _cache_path(self, video_path: Path, start: float, end: float) -> Path:
        folder = f"{video_path.stem}-{video_path.stat().st_size}"
        span = f"{round(start * 1000)}-{round(end * 1000)}"
        return self._cache_dir / folder / f"{self._name}-{span}.json"

    def find(
        self, video_path: Path, start_seconds: float, end_seconds: float
    ) -> FacecamBox | None:
        if not video_path.is_file():
            raise FaceError(f"Input file not found: {video_path}")
        cache_file = self._cache_path(video_path, start_seconds, end_seconds)
        if cache_file.is_file():
            try:
                found = _Found.model_validate_json(cache_file.read_bytes())
                logger.info("Facecam cache hit: %s", cache_file.name)
                return found.box
            except (ValidationError, OSError):
                logger.warning("Ignoring unreadable cache file %s", cache_file)

        box = self._inner.find(video_path, start_seconds, end_seconds)
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(_Found(box=box).model_dump_json(), encoding="utf-8")
        except OSError as exc:
            logger.warning("Cannot write facecam cache %s: %s", cache_file, exc)
        return box
