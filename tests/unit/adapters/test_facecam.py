import io
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from clipper.adapters.media import facecam
from clipper.adapters.media.facecam import (
    CachingFacecamFinder,
    OpenCvFacecamFinder,
    facecam_box,
)
from clipper.core.interfaces import FacecamFinder
from clipper.domain.errors import FaceError
from clipper.domain.models import FacecamBox

# Constructed frames and faces; nothing here runs ffmpeg or the model. The
# frame is 1280x720 of faint noise with a bright 2 px border round a cam box
# at the bottom right, as Sonoket Gaming's sits (ADR 0025, facecam spike).
W, H = 1280, 720
CAM = (936, 493, 1270, 703)
FACE = (1060.0, 540.0, 60.0, 80.0)


def _frame(border: tuple[int, int, int, int] | None = CAM, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    gray = rng.integers(96, 104, size=(H, W), dtype=np.uint8)
    if border is not None:
        x0, y0, x1, y1 = border
        cv2.rectangle(gray, (x0, y0), (x1, y1), 230, 2)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def _grads(frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
    gy = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
    return gx, gy


def _faces(frames: int, face: tuple[float, float, float, float] = FACE) -> np.ndarray:
    return np.array([[i, *face] for i in range(frames)], np.float64)


def _pixels(box: FacecamBox | None) -> tuple[int, int, int, int] | None:
    if box is None:
        return None
    return (
        round(box.x * W),
        round(box.y * H),
        round((box.x + box.w) * W),
        round((box.y + box.h) * H),
    )


def _close(found: tuple[int, int, int, int] | None, want: tuple[int, ...]) -> bool:
    if found is None:
        return False
    return all(abs(a - b) <= 3 for a, b in zip(found, want, strict=True))


def test_finds_the_bordered_box_round_a_still_face() -> None:
    gx, gy = _grads(_frame())
    assert _close(_pixels(facecam_box(_faces(10), 10, gx, gy)), CAM)


def test_no_face_or_a_face_in_too_few_frames_is_no_box() -> None:
    gx, gy = _grads(_frame())
    assert facecam_box(np.zeros((0, 5)), 10, gx, gy) is None
    assert facecam_box(_faces(4), 10, gx, gy) is None


def test_a_face_that_moves_is_no_box() -> None:
    gx, gy = _grads(_frame())
    moving = np.array([[i, 100.0 + 90 * i, 300.0, 60.0, 80.0] for i in range(10)])
    assert facecam_box(moving, 10, gx, gy) is None


def test_a_camera_with_no_border_is_no_box() -> None:
    # A full-frame webcam: the sides fall to the frame's edges.
    gx, gy = _grads(_frame(border=None))
    assert facecam_box(_faces(10), 10, gx, gy) is None


def test_a_box_too_small_for_a_facecam_is_no_box() -> None:
    # A player card round a face, 100 px wide (8% of the frame).
    card = (1010, 500, 1110, 640)
    gx, gy = _grads(_frame(border=card))
    assert facecam_box(_faces(10, (1040.0, 540.0, 40.0, 50.0)), 10, gx, gy) is None


def test_a_second_still_face_is_tried_when_the_first_has_no_box() -> None:
    # A face in the gameplay, still in every frame, and the cam's face.
    gx, gy = _grads(_frame())
    faces = np.vstack([_faces(10, (300.0, 100.0, 40.0, 50.0)), _faces(8)])
    assert _close(_pixels(facecam_box(faces, 10, gx, gy)), CAM)


# The finder end to end, on piped constructed frames.
class FakeProcess:
    def __init__(self, frame: np.ndarray, frames: int, code: int = 0) -> None:
        self.stdout = io.BytesIO(frame.tobytes() * frames + b"\x00" * 10)
        self.returncode = code

    def communicate(self) -> tuple[bytes, bytes]:
        return b"", b"noise\nInvalid data found\n"


class FakeDetector:
    def detect(self, frame: np.ndarray) -> tuple[int, np.ndarray]:
        row = np.zeros((1, 15), dtype=np.float32)
        row[0, :4] = FACE
        return 1, row


@pytest.fixture
def video(tmp_path: Path) -> Path:
    path = tmp_path / "game.mp4"
    path.write_bytes(b"not a real video")
    return path


def _pipe(monkeypatch: pytest.MonkeyPatch, code: int = 0) -> list[list[str]]:
    commands: list[list[str]] = []

    def popen(command: list[str], **kwargs: object) -> FakeProcess:
        commands.append(command)
        return FakeProcess(_frame(), 4, code)

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(facecam, "load_face_model", lambda model: FakeDetector())
    return commands


def test_satisfies_protocol() -> None:
    assert isinstance(OpenCvFacecamFinder(), FacecamFinder)
    finder = CachingFacecamFinder(OpenCvFacecamFinder(), Path(), "x")
    assert isinstance(finder, FacecamFinder)


def test_the_finder_reads_frames_at_two_a_second(
    video: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands = _pipe(monkeypatch)
    box = OpenCvFacecamFinder().find(video, 10.0, 12.0)
    assert _close(_pixels(box), CAM)
    command = commands[0]
    assert command[command.index("-ss") + 1] == "10.000"
    assert command[command.index("-t") + 1] == "2.000"
    assert command[command.index("-vf") + 1] == "fps=2,scale=1280:720"


def test_a_failed_ffmpeg_reports_its_stderr(
    video: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pipe(monkeypatch, code=1)
    with pytest.raises(FaceError, match=r"^ffmpeg failed \(exit 1\): noise\nInvalid"):
        OpenCvFacecamFinder().find(video, 0.0, 1.0)


def test_a_missing_video_or_ffmpeg_is_an_error(video: Path) -> None:
    with pytest.raises(FaceError, match="Input file not found"):
        OpenCvFacecamFinder().find(video.with_name("gone.mp4"), 0.0, 1.0)
    with pytest.raises(FaceError, match="'no-such-ffmpeg' not found on PATH"):
        OpenCvFacecamFinder(executable="no-such-ffmpeg").find(video, 0.0, 1.0)


class CountingFinder:
    def __init__(self, box: FacecamBox | None) -> None:
        self.calls = 0
        self._box = box

    def find(
        self, video_path: Path, start_seconds: float, end_seconds: float
    ) -> FacecamBox | None:
        self.calls += 1
        return self._box


@pytest.mark.parametrize("box", [FacecamBox(x=0.7, y=0.6, w=0.25, h=0.3), None])
def test_the_cache_keeps_each_answer_once(
    video: Path, tmp_path: Path, box: FacecamBox | None
) -> None:
    inner = CountingFinder(box)
    finder = CachingFacecamFinder(inner, tmp_path / "cache", "name")
    assert finder.find(video, 1.0, 2.5) == box
    assert finder.find(video, 1.0, 2.5) == box
    assert inner.calls == 1
    [cached] = (tmp_path / "cache").rglob("*.json")
    assert cached.name == "name-1000-2500.json"


def test_an_unreadable_cache_file_is_found_again(video: Path, tmp_path: Path) -> None:
    inner = CountingFinder(None)
    finder = CachingFacecamFinder(inner, tmp_path, "name")
    finder.find(video, 0.0, 1.0)
    [cached] = tmp_path.rglob("*.json")
    cached.write_text("{nope", encoding="utf-8")
    assert finder.find(video, 0.0, 1.0) is None
    assert inner.calls == 2
