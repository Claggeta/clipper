import io
import subprocess
from pathlib import Path

import numpy as np
import pytest

from clipper.adapters.media import speakers
from clipper.adapters.media.faces import FRAME_HEIGHT, FRAME_WIDTH
from clipper.adapters.media.speakers import (
    SCORER_NAME,
    CachingSpeakerScorer,
    OpenCvSpeakerScorer,
    crop_face,
    median_filter,
    mfcc,
    score_face,
)
from clipper.core.interfaces import SpeakerScorer
from clipper.domain.errors import FaceError
from clipper.domain.models import FaceBox, FaceSample, FaceTrack, SpeakerTrack

# Constructed frames, sound and faces; nothing here runs ffmpeg or the model,
# except the model-file checks at the end.
_FRAME = bytes(FRAME_WIDTH * FRAME_HEIGHT * 3)
_FACE = FaceBox(x=0.4, y=0.3, w=0.1, h=0.2, score=0.9)


def tone() -> np.ndarray:
    t = np.arange(8000) / 16000
    wave: np.ndarray = 3000 * np.sin(2 * np.pi * 440 * t) + 1000 * np.sin(
        2 * np.pi * 1234 * t
    )
    return wave.astype(np.int16)


class FakeProcess:
    def __init__(self, stdout: bytes, code: int = 0, stderr: bytes = b"") -> None:
        self.stdout = io.BytesIO(stdout)
        self.returncode = code
        self._stderr = stderr

    def communicate(self) -> tuple[bytes, bytes]:
        return b"", self._stderr


class FakeNet:
    """Scores every frame it is given 1.0, and counts the windows."""

    def __init__(self) -> None:
        self.inputs: dict[str, np.ndarray] = {}
        self.windows = 0

    def setInput(self, blob: np.ndarray, name: str) -> None:  # noqa: N802
        self.inputs[name] = blob

    def forward(self) -> np.ndarray:
        self.windows += 1
        audio, video = self.inputs["audio"], self.inputs["video"]
        assert audio.dtype == video.dtype == np.float32
        assert audio.shape[1] == 4 * video.shape[1]
        return np.ones(video.shape[1], dtype=np.float32)


class FakeTracker:
    def __init__(self, faces: tuple[FaceBox, ...] = (_FACE,), samples: int = 5) -> None:
        self.faces = faces
        self.samples = samples

    def track(
        self, video_path: Path, start_seconds: float, end_seconds: float
    ) -> FaceTrack:
        return FaceTrack(
            source=video_path.name,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            samples_per_second=5.0,
            samples=tuple(
                FaceSample(seconds=start_seconds + i / 5, faces=self.faces)
                for i in range(self.samples)
            ),
        )


@pytest.fixture
def video(tmp_path: Path) -> Path:
    path = tmp_path / "talk.mp4"
    path.write_bytes(b"not a real video")
    return path


@pytest.fixture
def piped(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """ffmpeg gives 1 s of frames, then 1 s of sound."""
    commands: list[list[str]] = []

    def popen(command: list[str], **kwargs: object) -> FakeProcess:
        commands.append(command)
        if "s16le" in command:
            return FakeProcess(np.zeros(16000, np.int16).tobytes())
        return FakeProcess(_FRAME * 25 + b"\x00" * 10)

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(OpenCvSpeakerScorer, "_net", lambda self: FakeNet())
    return commands


def test_satisfies_protocol() -> None:
    assert isinstance(OpenCvSpeakerScorer(FakeTracker()), SpeakerScorer)
    assert isinstance(
        CachingSpeakerScorer(OpenCvSpeakerScorer(FakeTracker()), Path(), ""),
        SpeakerScorer,
    )


def test_mfcc_matches_python_speech_features() -> None:
    # From python_speech_features 0.6's mfcc(signal, 16000) on the same tone.
    features = mfcc(tone())

    assert features.shape == (49, 13)
    assert features[0] == pytest.approx(
        [17.696999, 17.085004, -11.548753, -21.316593, -10.578455, -7.180252,
         -24.805368, -40.187522, -21.547863, 19.591322, 42.547445, 26.818002,
         -2.503301],
        abs=1e-5,
    )  # fmt: skip
    assert features[48] == pytest.approx(
        [17.465007, 19.404347, -8.813728, -16.633994, -4.747762, -2.982814,
         -23.454352, -39.854528, -20.349017, 21.135822, 42.94729, 25.544856,
         -4.670811],
        abs=1e-5,
    )  # fmt: skip


def test_mfcc_of_short_silent_and_empty_sound() -> None:
    assert mfcc(tone()[:300]).shape == (1, 13)
    assert mfcc(np.zeros(800, np.int16))[0, 0] == pytest.approx(-36.0437, abs=1e-4)
    assert mfcc(np.zeros(0, np.int16)).shape == (0, 13)


def test_median_filter_pads_with_zeros_as_scipy() -> None:
    assert median_filter(np.array([1, 5, 2, 8, 3]), 3).tolist() == [1, 2, 5, 3, 3]


def test_a_face_crop_is_112_grey_and_padded_past_the_frame() -> None:
    frame = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), np.uint8)
    frame[300:400, 600:700] = 255

    middle = crop_face(frame, 650.0, 350.0, 50.0)
    corner = crop_face(frame, 0.0, 0.0, 50.0)

    assert middle.shape == corner.shape == (112, 112)
    assert middle.dtype == np.uint8
    assert middle[40, 56] == 255
    assert corner[0, 0] == 110


def test_scores_average_the_windows_and_drop_the_last_frame() -> None:
    net = FakeNet()
    faces = np.zeros((30, 112, 112), np.uint8)
    # 30 frames of sound make 119 MFCC rows; whole fours give 1.16 s.
    scores = score_face(net, np.zeros((119, 13)), faces)  # type: ignore[arg-type]

    assert scores == (1.0,) * 29
    # Two 1 s windows thrice, one 2 s window thrice, then one each.
    assert net.windows == 2 * 3 + 1 * 3 + 1 * 5


def test_scores_each_face_frame_by_frame(video: Path, piped: list[list[str]]) -> None:
    track = OpenCvSpeakerScorer(FakeTracker()).score(video, 10.0, 11.0)

    assert track.source == "talk.mp4"
    (face,) = track.faces
    assert face.seconds == pytest.approx((10.0, 10.2, 10.4, 10.6, 10.8))
    assert face.boxes == (_FACE,) * 5
    assert face.frames_per_second == 25
    # Detections span frames 0 to 20; the last frame has no score.
    assert face.scores == (1.0,) * 20
    video_command, audio_command = piped
    assert "fps=25,scale=1280:720" in video_command
    assert (
        audio_command[-7:]
        == ["-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"][-7:]
    )
    assert video_command[video_command.index("-ss") + 1] == "10.000"


def test_no_faces_means_no_decoding(video: Path, piped: list[list[str]]) -> None:
    track = OpenCvSpeakerScorer(FakeTracker(faces=())).score(video, 10.0, 11.0)

    assert track.faces == ()
    assert piped == []


def test_a_failed_ffmpeg_reports_its_stderr(
    video: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda command, **_: FakeProcess(b"", 1, b"talk.mp4: Invalid data"),
    )
    monkeypatch.setattr(OpenCvSpeakerScorer, "_net", lambda self: FakeNet())

    with pytest.raises(FaceError, match="Invalid data"):
        OpenCvSpeakerScorer(FakeTracker()).score(video, 0.0, 1.0)


def test_a_missing_video_or_ffmpeg_is_an_error(
    video: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scorer = OpenCvSpeakerScorer(FakeTracker())
    with pytest.raises(FaceError, match="Input file not found"):
        scorer.score(video.with_name("gone.mp4"), 0.0, 1.0)
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(FaceError, match="not found on PATH"):
        scorer.score(video, 0.0, 1.0)


def test_a_missing_or_bad_model_is_an_error(video: Path, tmp_path: Path) -> None:
    with pytest.raises(FaceError, match="Speaker model not found"):
        OpenCvSpeakerScorer(FakeTracker(), model=tmp_path / "gone.onnx")._net()
    bad = tmp_path / "bad.onnx"
    bad.write_bytes(b"not a model")
    with pytest.raises(FaceError, match="Cannot load speaker model"):
        OpenCvSpeakerScorer(FakeTracker(), model=bad)._net()


class CountingScorer:
    def __init__(self) -> None:
        self.calls: list[tuple[float, float]] = []

    def score(
        self, video_path: Path, start_seconds: float, end_seconds: float
    ) -> SpeakerTrack:
        self.calls.append((start_seconds, end_seconds))
        return SpeakerTrack(
            source=video_path.name, start_seconds=start_seconds, end_seconds=end_seconds
        )


def test_the_cache_keeps_each_span_once(video: Path, tmp_path: Path) -> None:
    inner = CountingScorer()
    scorer = CachingSpeakerScorer(inner, tmp_path / "speakers", SCORER_NAME)

    first = scorer.score(video, 1.0, 2.5)
    again = scorer.score(video, 1.0, 2.5)
    scorer.score(video, 3.0, 4.0)

    assert first == again
    assert inner.calls == [(1.0, 2.5), (3.0, 4.0)]
    saved = tmp_path / "speakers" / "talk-16" / f"{SCORER_NAME}-1000-2500.json"
    assert SpeakerTrack.model_validate_json(saved.read_bytes()) == first


def test_an_unreadable_cache_file_is_scored_again(video: Path, tmp_path: Path) -> None:
    inner = CountingScorer()
    path = tmp_path / "speakers" / "talk-16" / "s-0-1000.json"
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")

    CachingSpeakerScorer(inner, tmp_path / "speakers", "s").score(video, 0.0, 1.0)

    assert inner.calls == [(0.0, 1.0)]
    assert SpeakerTrack.model_validate_json(path.read_bytes()).source == "talk.mp4"


def test_a_cache_that_cannot_be_written_still_returns(
    video: Path, tmp_path: Path
) -> None:
    blocked = tmp_path / "speakers"
    blocked.write_text("a file where the folder should be", encoding="utf-8")

    track = CachingSpeakerScorer(CountingScorer(), blocked, "s").score(video, 0.0, 1.0)

    assert track.source == "talk.mp4"


def test_the_cache_needs_the_video(video: Path, tmp_path: Path) -> None:
    with pytest.raises(FaceError, match="Input file not found"):
        CachingSpeakerScorer(CountingScorer(), tmp_path, "s").score(
            video.with_name("gone.mp4"), 0.0, 1.0
        )


def test_the_model_ships_with_its_licence() -> None:
    import hashlib

    digest = hashlib.sha256(speakers.MODEL_PATH.read_bytes()).hexdigest()
    assert digest == "08eb2d23b73f2fdf67fd55fbe42387543f9e95c218af2bd37802ac9e2fba1b2a"
    licence = speakers.MODEL_PATH.with_name("LICENSE-light-asd").read_text(
        encoding="utf-8"
    )
    assert licence.startswith("MIT License")
    assert "Liao Junhua" in licence
