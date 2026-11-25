import logging
import math
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

import cv2
import numpy as np
from pydantic import ValidationError

from clipper.adapters.media._process import format_failure
from clipper.adapters.media.faces import FRAME_HEIGHT, FRAME_WIDTH, TRACKER_NAME
from clipper.core.interfaces import FaceTracker, SpeakerScorer
from clipper.core.speakers import link_faces
from clipper.domain.errors import FaceError
from clipper.domain.models import FaceBox, SpeakerTrack, TalkingFace

logger = logging.getLogger(__name__)

# Light-ASD (Liao et al., CVPR 2023), MIT licence (LICENSE-light-asd beside
# it), its AVA weights exported to ONNX (ADR 0025, ONNX amendment). Faces at
# 25 fps, audio at 16 kHz, as it was trained.
MODEL_PATH = Path(__file__).parent / "models" / "light_asd.onnx"
FRAMES_PER_SECOND = 25
SAMPLE_RATE = 16000
# Its demo's crop: the box's half side padded by 0.40, smoothed over 13
# frames, the face resized to 224 and its middle 112 kept, in grey.
CROP_SCALE = 0.40
SMOOTH_FRAMES = 13
CROP_SIDE = 112
# Its demo scores each face in windows of these many seconds and averages
# them, which it found more reliable than one length.
WINDOW_SECONDS = (1, 1, 1, 2, 2, 2, 3, 3, 4, 5, 6)
# Names the model, settings and the face tracker in the cache.
SCORER_NAME = f"lightasd-ava-25fps-{TRACKER_NAME}"

_FRAME_BYTES = FRAME_WIDTH * FRAME_HEIGHT * 3
_SAMPLES_PER_FRAME = SAMPLE_RATE // FRAMES_PER_SECOND


class OpenCvSpeakerScorer:
    """Scores who is talking with Light-ASD in OpenCV's DNN module.

    Faces come from the face tracker (its cached 5 fps samples, ADR 0025),
    linked into tracks and filled in at 25 fps; ffmpeg decodes the span's
    frames at 1280x720 and its audio at 16 kHz. See docs/decisions/0025.
    """

    def __init__(
        self, faces: FaceTracker, executable: str = "ffmpeg", model: Path = MODEL_PATH
    ) -> None:
        self._faces = faces
        self._executable = executable
        self._model = model

    def score(
        self, video_path: Path, start_seconds: float, end_seconds: float
    ) -> SpeakerTrack:
        if not video_path.is_file():
            raise FaceError(f"Input file not found: {video_path}")
        if shutil.which(self._executable) is None:
            raise FaceError(
                f"'{self._executable}' not found on PATH. Install ffmpeg first."
            )
        net = self._net()
        tracks = link_faces(self._faces.track(video_path, start_seconds, end_seconds))
        found: list[TalkingFace] = []
        if tracks:
            paths = [_frame_path(track, start_seconds) for track in tracks]
            crops = self._crops(video_path, start_seconds, end_seconds, paths)
            audio = self._audio(video_path, start_seconds, end_seconds)
            for track, (first, _), faces in zip(tracks, paths, crops, strict=True):
                if not faces:
                    continue
                begin = first * _SAMPLES_PER_FRAME
                sound = audio[begin : begin + len(faces) * _SAMPLES_PER_FRAME]
                found.append(
                    TalkingFace(
                        seconds=tuple(seconds for seconds, _ in track),
                        boxes=tuple(box for _, box in track),
                        frames_per_second=FRAMES_PER_SECOND,
                        scores=score_face(net, mfcc(sound), np.stack(faces)),
                    )
                )
        return SpeakerTrack(
            source=video_path.name,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            faces=tuple(found),
        )

    def _net(self) -> cv2.dnn.Net:
        if not self._model.is_file():
            raise FaceError(f"Speaker model not found: {self._model}")
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
        try:
            return cv2.dnn.readNetFromONNX(str(self._model))
        except cv2.error as exc:
            raise FaceError(f"Cannot load speaker model {self._model}: {exc}") from exc

    def _run(
        self, video_path: Path, start: float, end: float, output: list[str]
    ) -> list[str]:
        return [
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{end - start:.3f}",
            "-i",
            str(video_path),
            *output,
            "-",
        ]

    def _crops(
        self,
        video_path: Path,
        start: float,
        end: float,
        paths: Sequence[tuple[int, np.ndarray]],
    ) -> list[list[np.ndarray]]:
        """Each track's face crops, frame by frame while the span decodes."""
        args = self._run(
            video_path,
            start,
            end,
            [
                "-vf",
                f"fps={FRAMES_PER_SECOND},scale={FRAME_WIDTH}:{FRAME_HEIGHT}",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "bgr24",
            ],
        )
        # Frames are cropped as they come, not held: a second is 25 frames of
        # 2.7 MB. stderr stays small with `-loglevel error`.
        process = subprocess.Popen(
            [self._executable, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        assert process.stdout is not None
        crops: list[list[np.ndarray]] = [[] for _ in paths]
        index = 0
        while len(chunk := process.stdout.read(_FRAME_BYTES)) == _FRAME_BYTES:
            frame = np.frombuffer(chunk, np.uint8).reshape(FRAME_HEIGHT, FRAME_WIDTH, 3)
            for out, (first, path) in zip(crops, paths, strict=True):
                if first <= index < first + len(path):
                    out.append(crop_face(frame, *path[index - first]))
            index += 1
        self._check(args, process)
        return crops

    def _audio(self, video_path: Path, start: float, end: float) -> np.ndarray:
        args = self._run(
            video_path,
            start,
            end,
            ["-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le"],
        )
        process = subprocess.Popen(
            [self._executable, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        assert process.stdout is not None
        audio = np.frombuffer(process.stdout.read(), np.int16)
        self._check(args, process)
        return audio

    def _check(self, args: list[str], process: subprocess.Popen[bytes]) -> None:
        _, stderr = process.communicate()
        if process.returncode != 0:
            text = stderr.decode("utf-8", errors="replace")
            logger.info("%s stderr:\n%s", self._executable, text)
            result = subprocess.CompletedProcess(args, process.returncode, "", text)
            raise FaceError(format_failure(self._executable, result))


def _frame_path(
    track: Sequence[tuple[float, FaceBox]], start_seconds: float
) -> tuple[int, np.ndarray]:
    """A track's first 25 fps frame and, from there, its crop centre and half
    side in pixels per frame: boxes filled in between detections, then
    median-smoothed, as Light-ASD's demo does."""
    frames = [
        round((seconds - start_seconds) * FRAMES_PER_SECOND) for seconds, _ in track
    ]
    every = np.arange(frames[0], frames[-1] + 1)
    half = [max(b.w * FRAME_WIDTH, b.h * FRAME_HEIGHT) / 2 for _, b in track]
    x = [(b.x + b.w / 2) * FRAME_WIDTH for _, b in track]
    y = [(b.y + b.h / 2) * FRAME_HEIGHT for _, b in track]
    path = np.stack(
        [
            median_filter(np.interp(every, frames, values), SMOOTH_FRAMES)
            for values in (x, y, half)
        ],
        axis=1,
    )
    return frames[0], path


def median_filter(values: np.ndarray, size: int) -> np.ndarray:
    """A running median over `size` values, zero-padded at the ends, as
    `scipy.signal.medfilt`."""
    pad = size // 2
    padded = np.pad(np.asarray(values, dtype=float), pad)
    windows = np.lib.stride_tricks.sliding_window_view(padded, size)
    return np.asarray(np.median(windows, axis=1), dtype=float)


def crop_face(frame: np.ndarray, x: float, y: float, half: float) -> np.ndarray:
    """Light-ASD's face crop: a square from just above the face to below the
    chin, padded with grey past the frame, resized to 224 and its middle
    112 kept, in grey."""
    pad = int(half * (1 + 2 * CROP_SCALE))
    padded = cv2.copyMakeBorder(
        frame, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(110, 110, 110)
    )
    my, mx = y + pad, x + pad
    face = padded[
        int(my - half) : int(my + half * (1 + 2 * CROP_SCALE)),
        int(mx - half * (1 + CROP_SCALE)) : int(mx + half * (1 + CROP_SCALE)),
    ]
    grey = cv2.cvtColor(cv2.resize(face, (224, 224)), cv2.COLOR_BGR2GRAY)
    edge = (224 - CROP_SIDE) // 2
    return grey[edge : edge + CROP_SIDE, edge : edge + CROP_SIDE]


def score_face(
    net: cv2.dnn.Net, features: np.ndarray, faces: np.ndarray
) -> tuple[float, ...]:
    """Light-ASD's speaking score per frame, averaged over `WINDOW_SECONDS`.

    `features` are MFCC rows at 100 a second, `faces` the 112 px crops at
    25 fps. As its demo, the length is the audio's whole multiples of 4
    rows, so the last frame or so has no score.
    """
    seconds = min(
        (len(features) - len(features) % 4) / 100, len(faces) / FRAMES_PER_SECOND
    )
    features = features[: round(seconds * 100)].astype(np.float32)
    faces = faces[: round(seconds * FRAMES_PER_SECOND)].astype(np.float32)
    if not len(faces):
        return ()
    runs = []
    for window in WINDOW_SECONDS:
        scores: list[float] = []
        for i in range(math.ceil(seconds / window)):
            net.setInput(
                features[None, i * window * 100 : (i + 1) * window * 100], "audio"
            )
            net.setInput(
                faces[
                    None,
                    i * window * FRAMES_PER_SECOND : (i + 1)
                    * window
                    * FRAMES_PER_SECOND,
                ],
                "video",
            )
            scores.extend(float(value) for value in net.forward().ravel())
        runs.append(scores)
    shortest = min(len(run) for run in runs)
    mean = np.mean([run[:shortest] for run in runs], axis=0)
    return tuple(round(float(value), 2) for value in mean)


def mfcc(signal: np.ndarray, rate: int = SAMPLE_RATE) -> np.ndarray:
    """13 mel cepstral coefficients per 25 ms, every 10 ms, as
    `python_speech_features.mfcc` with its defaults: pre-emphasis 0.97, a
    512-point FFT, 26 mel filters, liftering 22, the first coefficient
    replaced by the frame's log energy."""
    signal = np.asarray(signal, dtype=float)
    if len(signal) == 0:
        return np.zeros((0, 13))
    signal = np.append(signal[0], signal[1:] - 0.97 * signal[:-1])
    length, step, nfft = round(0.025 * rate), round(0.010 * rate), 512
    count = 1 if len(signal) <= length else 1 + math.ceil((len(signal) - length) / step)
    signal = np.append(signal, np.zeros((count - 1) * step + length - len(signal)))
    frames = signal[np.arange(length)[None, :] + step * np.arange(count)[:, None]]
    power = np.abs(np.fft.rfft(frames, nfft)) ** 2 / nfft
    eps = np.finfo(float).eps
    energy = np.sum(power, axis=1)
    energy[energy == 0] = eps
    banks = power @ _mel_filters(26, nfft, rate).T
    banks[banks == 0] = eps
    coefficients: np.ndarray = np.log(banks) @ _dct(26)[:13].T
    coefficients *= 1 + 11 * np.sin(np.pi * np.arange(13) / 22)
    coefficients[:, 0] = np.log(energy)
    return coefficients


def _mel_filters(count: int, nfft: int, rate: int) -> np.ndarray:
    def to_mel(hz: np.ndarray) -> np.ndarray:
        return 2595 * np.log10(1 + hz / 700.0)

    edges = np.linspace(to_mel(np.array(0.0)), to_mel(np.array(rate / 2)), count + 2)
    bins = np.floor((nfft + 1) * 700 * (10 ** (edges / 2595.0) - 1) / rate)
    filters = np.zeros((count, nfft // 2 + 1))
    for j in range(count):
        low, mid, high = int(bins[j]), int(bins[j + 1]), int(bins[j + 2])
        for i in range(low, mid):
            filters[j, i] = (i - bins[j]) / (bins[j + 1] - bins[j])
        for i in range(mid, high):
            filters[j, i] = (bins[j + 2] - i) / (bins[j + 2] - bins[j + 1])
    return filters


def _dct(size: int) -> np.ndarray:
    """The orthonormal type II DCT as a matrix, rows by coefficient."""
    k, n = np.arange(size)[:, None], np.arange(size)[None, :]
    matrix: np.ndarray = np.sqrt(2 / size) * np.cos(
        np.pi * k * (2 * n + 1) / (2 * size)
    )
    matrix[0] /= np.sqrt(2)
    return matrix


class CachingSpeakerScorer:
    """Wraps a SpeakerScorer and keeps each span's scores as JSON on disk.

    One file per span, `<cache dir>/<video stem>-<size>/<name>-<start ms>-<end
    ms>.json`, as the face cache (ADR 0025).
    """

    def __init__(self, inner: SpeakerScorer, cache_dir: Path, name: str) -> None:
        self._inner = inner
        self._cache_dir = cache_dir
        self._name = name

    def _cache_path(self, video_path: Path, start: float, end: float) -> Path:
        folder = f"{video_path.stem}-{video_path.stat().st_size}"
        span = f"{round(start * 1000)}-{round(end * 1000)}"
        return self._cache_dir / folder / f"{self._name}-{span}.json"

    def score(
        self, video_path: Path, start_seconds: float, end_seconds: float
    ) -> SpeakerTrack:
        if not video_path.is_file():
            raise FaceError(f"Input file not found: {video_path}")
        cache_file = self._cache_path(video_path, start_seconds, end_seconds)
        if cache_file.is_file():
            try:
                track = SpeakerTrack.model_validate_json(cache_file.read_bytes())
                logger.info("Speaker cache hit: %s", cache_file.name)
                return track
            except (ValidationError, OSError):
                logger.warning("Ignoring unreadable cache file %s", cache_file)

        track = self._inner.score(video_path, start_seconds, end_seconds)
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(track.model_dump_json(), encoding="utf-8")
        except OSError as exc:
            logger.warning("Cannot write speaker cache %s: %s", cache_file, exc)
        return track
