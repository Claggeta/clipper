import struct
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from clipper.adapters.media.peaks import (
    PEAKS_PER_SECOND,
    SAMPLE_RATE,
    FfmpegPeaksExtractor,
    peaks_from_pcm,
)
from clipper.core.interfaces import PeaksExtractor
from clipper.domain.errors import ClipperError, PeaksError

# Constructed samples, not decoded audio.
PER_PEAK = SAMPLE_RATE // PEAKS_PER_SECOND


def _pcm(samples: list[int]) -> bytes:
    return struct.pack(f"<{len(samples)}h", *samples)


def _video(tmp_path: Path) -> Path:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    return video


def _run(
    returncode: int, stderr: str, captured: list[list[str]], pcm: bytes
) -> Callable[..., subprocess.CompletedProcess[str]]:
    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured.append(command)
        if returncode == 0:
            Path(command[-1]).write_bytes(pcm)
        return subprocess.CompletedProcess(command, returncode, "", stderr)

    return fake_run


def test_peaks_are_the_loudest_sample_per_stretch_scaled_to_a_byte(
    tmp_path: Path,
) -> None:
    pcm = tmp_path / "a.pcm"
    # Silence; a quiet stretch with one negative peak; full scale both
    # ways; and a short last stretch.
    pcm.write_bytes(
        _pcm(
            [0] * PER_PEAK
            + [100] * (PER_PEAK - 1)
            + [-8192]
            + [32767, -32768] * (PER_PEAK // 2)
            + [16384] * 3
        )
    )
    assert list(peaks_from_pcm(pcm)) == [0, 64, 255, 128]


def test_an_empty_file_has_no_peaks(tmp_path: Path) -> None:
    pcm = tmp_path / "a.pcm"
    pcm.write_bytes(b"")
    assert peaks_from_pcm(pcm) == b""


def test_satisfies_protocol() -> None:
    assert isinstance(FfmpegPeaksExtractor(), PeaksExtractor)


def test_missing_input_raises(tmp_path: Path) -> None:
    with pytest.raises(PeaksError, match="not found") as info:
        FfmpegPeaksExtractor().extract(tmp_path / "nope.mp4", tmp_path / "p.bin")
    assert isinstance(info.value, ClipperError)


def test_missing_ffmpeg_raises(tmp_path: Path) -> None:
    extractor = FfmpegPeaksExtractor(executable="definitely-not-ffmpeg")
    with pytest.raises(PeaksError, match="PATH"):
        extractor.extract(_video(tmp_path), tmp_path / "p.bin")


def test_success_decodes_the_first_audio_stream_and_deletes_the_samples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = _video(tmp_path)
    out = tmp_path / "peaks" / "v.bin"
    captured: list[list[str]] = []
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        subprocess, "run", _run(0, "", captured, _pcm([16384] * PER_PEAK * 2))
    )

    assert FfmpegPeaksExtractor().extract(video, out) == out

    assert out.read_bytes() == bytes([128, 128])
    command = captured[0]
    assert command[:4] == ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    assert command[command.index("-i") + 1] == str(video)
    assert command[command.index("-map") + 1] == "0:a:0"
    assert command[command.index("-ar") + 1] == str(SAMPLE_RATE)
    assert command[command.index("-f") + 1] == "s16le"
    assert command[-1] == str(out.with_name("v.bin.pcm"))
    assert sorted(p.name for p in out.parent.iterdir()) == ["v.bin"]


def test_failure_names_ffmpeg_and_leaves_no_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "peaks" / "v.bin"
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
    stderr = "Stream map '0:a:0' matches no streams.\n"
    monkeypatch.setattr(subprocess, "run", _run(1, stderr, [], b""))

    with pytest.raises(PeaksError, match=r"ffmpeg failed \(exit 1\): Stream map"):
        FfmpegPeaksExtractor().extract(_video(tmp_path), out)
    assert list(out.parent.iterdir()) == []
