import io
import subprocess
from pathlib import Path

import numpy as np
import pytest

from clipper.adapters.media import faces
from clipper.adapters.media.faces import (
    FRAME_HEIGHT,
    FRAME_WIDTH,
    TRACKER_NAME,
    CachingFaceTracker,
    OpenCvFaceTracker,
)
from clipper.core.interfaces import FaceTracker
from clipper.domain.errors import FaceError
from clipper.domain.models import FaceBox, FaceSample, FaceTrack

# Constructed frames and detections; nothing here runs ffmpeg or the model,
# except the model-file checks at the end.
_FRAME = bytes(FRAME_WIDTH * FRAME_HEIGHT * 3)


class FakeProcess:
    def __init__(self, frames: int, code: int = 0, stderr: bytes = b"") -> None:
        self.stdout = io.BytesIO(_FRAME * frames + b"\x00" * 10)
        self.returncode = code
        self._stderr = stderr

    def communicate(self) -> tuple[bytes, bytes]:
        return b"", self._stderr


class FakeDetector:
    """Finds one face in every second frame, at a fixed place."""

    def __init__(self) -> None:
        self.calls = 0

    def detect(self, frame: np.ndarray) -> tuple[int, np.ndarray | None]:
        self.calls += 1
        assert frame.shape == (FRAME_HEIGHT, FRAME_WIDTH, 3)
        if self.calls % 2 == 0:
            return 1, None
        row = np.zeros((1, 15), dtype=np.float32)
        row[0, :4] = [320, 180, 128, 144]
        # A float32 holds 0.875 exactly, as YuNet's rows are float32.
        row[0, 14] = 0.875
        return 1, row


@pytest.fixture
def video(tmp_path: Path) -> Path:
    path = tmp_path / "talk.mp4"
    path.write_bytes(b"not a real video")
    return path


@pytest.fixture
def piped(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    commands: list[list[str]] = []

    def popen(command: list[str], **kwargs: object) -> FakeProcess:
        commands.append(command)
        return FakeProcess(frames=3)

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(OpenCvFaceTracker, "_detector", lambda self: FakeDetector())
    return commands


def test_satisfies_protocol() -> None:
    assert isinstance(OpenCvFaceTracker(), FaceTracker)
    assert isinstance(CachingFaceTracker(OpenCvFaceTracker(), Path(), "x"), FaceTracker)


def test_samples_each_frame_as_fractions_of_the_frame(
    video: Path, piped: list[list[str]]
) -> None:
    track = OpenCvFaceTracker().track(video, 10.0, 10.6)

    face = FaceBox(x=0.25, y=0.25, w=0.1, h=0.2, score=0.875)
    assert track == FaceTrack(
        source="talk.mp4",
        start_seconds=10.0,
        end_seconds=10.6,
        samples_per_second=5.0,
        samples=(
            FaceSample(seconds=10.0, faces=(face,)),
            FaceSample(seconds=10.2, faces=()),
            FaceSample(seconds=10.4, faces=(face,)),
        ),
    )
    command = piped[0]
    assert command[command.index("-ss") + 1] == "10.000"
    assert command[command.index("-t") + 1] == "0.600"
    assert command[command.index("-vf") + 1] == "fps=5,scale=1280:720"
    assert command[-5:] == ["-f", "rawvideo", "-pix_fmt", "bgr24", "-"]


def test_a_failed_ffmpeg_reports_its_stderr(
    video: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda command, **kwargs: FakeProcess(0, 1, b"noise\nInvalid data found\n"),
    )
    monkeypatch.setattr(OpenCvFaceTracker, "_detector", lambda self: FakeDetector())
    with pytest.raises(FaceError, match=r"^ffmpeg failed \(exit 1\): noise\nInvalid"):
        OpenCvFaceTracker().track(video, 0.0, 1.0)


def test_a_missing_video_or_ffmpeg_is_an_error(video: Path) -> None:
    with pytest.raises(FaceError, match="Input file not found"):
        OpenCvFaceTracker().track(video.with_name("gone.mp4"), 0.0, 1.0)
    with pytest.raises(FaceError, match="'no-such-ffmpeg' not found on PATH"):
        OpenCvFaceTracker(executable="no-such-ffmpeg").track(video, 0.0, 1.0)


def test_scores_over_one_and_empty_boxes_are_cleaned() -> None:
    rows = np.zeros((2, 15), dtype=np.float32)
    rows[0, :4] = [0, 0, 64, 72]
    rows[0, 14] = 1.02
    rows[1, :4] = [10, 10, 0, 5]
    assert faces._boxes(rows) == (FaceBox(x=0, y=0, w=0.05, h=0.1, score=1.0),)


def test_a_missing_model_is_an_error(video: Path) -> None:
    tracker = OpenCvFaceTracker(model=video.with_name("none.onnx"))
    with pytest.raises(FaceError, match="Face model not found"):
        tracker._detector()


def test_a_bad_model_file_is_an_error(video: Path) -> None:
    with pytest.raises(FaceError, match="Cannot load face model"):
        OpenCvFaceTracker(model=video)._detector()


class CountingTracker:
    def __init__(self) -> None:
        self.calls: list[tuple[float, float]] = []

    def track(
        self, video_path: Path, start_seconds: float, end_seconds: float
    ) -> FaceTrack:
        self.calls.append((start_seconds, end_seconds))
        return FaceTrack(
            source=video_path.name,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            samples_per_second=5.0,
            samples=(FaceSample(seconds=start_seconds),),
        )


def test_the_cache_keeps_each_span_once(video: Path, tmp_path: Path) -> None:
    inner = CountingTracker()
    tracker = CachingFaceTracker(inner, tmp_path / "faces", TRACKER_NAME)

    first = tracker.track(video, 1.0, 2.5)
    again = tracker.track(video, 1.0, 2.5)
    other = tracker.track(video, 3.0, 4.0)

    assert first == again
    assert other.start_seconds == 3.0
    assert inner.calls == [(1.0, 2.5), (3.0, 4.0)]
    saved = tmp_path / "faces" / "talk-16" / f"{TRACKER_NAME}-1000-2500.json"
    assert FaceTrack.model_validate_json(saved.read_bytes()) == first


def test_an_unreadable_cache_file_is_tracked_again(video: Path, tmp_path: Path) -> None:
    inner = CountingTracker()
    tracker = CachingFaceTracker(inner, tmp_path / "faces", "t")
    path = tmp_path / "faces" / "talk-16" / "t-0-1000.json"
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")

    tracker.track(video, 0.0, 1.0)

    assert inner.calls == [(0.0, 1.0)]
    assert FaceTrack.model_validate_json(path.read_bytes()).source == "talk.mp4"


def test_a_cache_that_cannot_be_written_still_returns(
    video: Path, tmp_path: Path
) -> None:
    blocked = tmp_path / "faces"
    blocked.write_text("a file where the folder should be", encoding="utf-8")
    inner = CountingTracker()

    track = CachingFaceTracker(inner, blocked, "t").track(video, 0.0, 1.0)

    assert track.source == "talk.mp4"
    assert inner.calls == [(0.0, 1.0)]


def test_the_cache_needs_the_video(video: Path, tmp_path: Path) -> None:
    tracker = CachingFaceTracker(CountingTracker(), tmp_path, "t")
    with pytest.raises(FaceError, match="Input file not found"):
        tracker.track(video.with_name("gone.mp4"), 0.0, 1.0)


def test_the_model_ships_with_its_licence() -> None:
    import hashlib

    digest = hashlib.sha256(faces.MODEL_PATH.read_bytes()).hexdigest()
    assert digest == "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
    licence = faces.MODEL_PATH.with_name("LICENSE-yunet").read_text(encoding="utf-8")
    assert licence.startswith("MIT License")
