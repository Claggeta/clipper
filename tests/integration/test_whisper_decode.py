"""Smoke test for the real audio decode path used by faster-whisper.

Why this exists: faster-whisper decodes audio through PyAV. Unit tests and the
fake transcriber never reach that code, so an incompatible faster-whisper/av
pair passed CI and then failed on the first real run (see
docs/decisions/0003-pin-transcription-dependencies.md).

This test decodes a tiny WAV generated with the standard library. It needs no
model, no network and no ffmpeg binary, so it runs on every push.
"""

import math
import struct
import wave
from pathlib import Path

from faster_whisper import decode_audio

# Matches what FfmpegAudioExtractor produces: 16 kHz, mono, 16-bit PCM.
SAMPLE_RATE = 16_000
DURATION_SECONDS = 1
FREQUENCY_HZ = 440
AMPLITUDE = 0.5


def _sample(n: int) -> bytes:
    angle = 2 * math.pi * FREQUENCY_HZ * n / SAMPLE_RATE
    return struct.pack("<h", int(AMPLITUDE * 32767 * math.sin(angle)))


def _write_sine_wav(path: Path) -> Path:
    frames = b"".join(_sample(n) for n in range(SAMPLE_RATE * DURATION_SECONDS))
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(frames)
    return path


def test_decode_audio_reads_wav_through_pyav(tmp_path: Path) -> None:
    wav_path = _write_sine_wav(tmp_path / "tone.wav")

    audio = decode_audio(str(wav_path))

    expected_samples = SAMPLE_RATE * DURATION_SECONDS
    assert abs(len(audio) - expected_samples) <= expected_samples * 0.01
    assert str(audio.dtype) == "float32"
    assert float(abs(audio).max()) > 0.1, "decoded audio is silent"
