from pathlib import Path

import pytest

from clipper.adapters.detection.heuristic import HeuristicClipDetector
from clipper.adapters.transcription.fake import FakeTranscriber
from clipper.core.pipeline import AnalysisPipeline
from clipper.domain.errors import ClipperError
from clipper.domain.models import AnalysisResult


def _pipeline() -> AnalysisPipeline:
    return AnalysisPipeline(FakeTranscriber(), HeuristicClipDetector())


def _video(tmp_path: Path) -> Path:
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"x")
    return video


def test_pipeline_runs_end_to_end_with_fake(tmp_path: Path) -> None:
    result = _pipeline().run(_video(tmp_path))
    assert result.source == "talk.mp4"
    assert result.transcript.segments
    assert len(result.candidates) <= 5


def test_result_round_trips_through_json(tmp_path: Path) -> None:
    result = _pipeline().run(_video(tmp_path))
    loaded = AnalysisResult.model_validate_json(result.model_dump_json())
    assert loaded == result


def test_missing_video_raises_clipper_error(tmp_path: Path) -> None:
    with pytest.raises(ClipperError, match="not found"):
        _pipeline().run(tmp_path / "nope.mp4")
