"""Smoke test for the real speaker model in the installed OpenCV.

Why this exists: the unit tests fake the network, so nothing else checks
that this OpenCV build loads the bundled Light-ASD export and gives the
scores the export gave (docs/decisions/0025, ONNX amendment). Fixed inputs,
no video, ffmpeg or network, so it runs on every push.
"""

import numpy as np
import pytest

from clipper.adapters.media.speakers import OpenCvSpeakerScorer


def test_the_bundled_model_gives_the_exported_scores() -> None:
    net = OpenCvSpeakerScorer(faces=None)._net()  # type: ignore[arg-type]
    audio = (10 * np.sin(np.arange(100 * 13) / 7.0)).reshape(1, 100, 13)
    video = ((np.arange(25 * 112 * 112) * 7) % 256).reshape(1, 25, 112, 112)

    net.setInput(audio.astype(np.float32), "audio")
    net.setInput(video.astype(np.float32), "video")
    scores = net.forward().ravel()

    # onnxruntime's scores for the same inputs, when the model was exported.
    assert scores[:3] == pytest.approx([-2.6065, -2.8570, -3.1323], abs=1e-3)
    assert scores[-3:] == pytest.approx([-3.7523, -3.3128, -2.4048], abs=1e-3)
    assert len(scores) == 25
