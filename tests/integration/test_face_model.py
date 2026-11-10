"""Smoke test for the real face model in the installed OpenCV.

Why this exists: the unit tests fake the detector, so nothing else checks
that this OpenCV build loads the bundled YuNet model and runs it on a frame
of the size the tracker sends (docs/decisions/0025). A blank frame has no
face; it needs no video, no ffmpeg and no network, so it runs on every push.
"""

import numpy as np

from clipper.adapters.media import faces
from clipper.adapters.media.faces import FRAME_HEIGHT, FRAME_WIDTH, OpenCvFaceTracker


def test_the_bundled_model_runs_on_a_blank_frame() -> None:
    detector = OpenCvFaceTracker()._detector()
    frame = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)

    _, found = detector.detect(frame)

    assert faces._boxes(found) == ()
