from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from clipper.adapters.detection.heuristic import HeuristicClipDetector
from clipper.adapters.detection.llm import LlmClipDetector
from clipper.adapters.media.ffprobe import FfprobeMediaProbe
from clipper.adapters.media.frames import FfmpegFrameExtractor
from clipper.adapters.rendering.ffmpeg import FfmpegClipRenderer
from clipper.adapters.results.review_files import JsonReviewFiles
from clipper.adapters.titles.ollama import OllamaTitleWriter
from clipper.adapters.transcription.caching import CachingTranscriber
from clipper.adapters.transcription.fake import FakeTranscriber
from clipper.cli.context import (
    AppContext,
    DetectorName,
    build_detector,
    build_title_writer,
)
from clipper.cli.main import app
from clipper.core.config import Settings
from clipper.core.pipeline import AnalysisPipeline
from clipper.domain.errors import AudioExtractionError, DetectionError, TitleError
from clipper.domain.models import AnalysisResult, ClipCandidate, Transcript


def _fake_transcriber(settings: Settings) -> FakeTranscriber:
    return FakeTranscriber()


def _invoke(args: list[str], context: AppContext | None = None) -> Result:
    """Run the CLI with `context`, or the real one when it is None."""
    return CliRunner().invoke(app, args, obj=context)


def test_missing_file_shows_friendly_error(tmp_path: Path) -> None:
    result = _invoke(["analyze", str(tmp_path / "nope.mp4")])
    assert result.exit_code == 1
    assert "Error" in result.output


def test_error_with_bracketed_text_is_not_parsed_as_markup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(self: AnalysisPipeline, video: Path) -> object:
        raise AudioExtractionError("[aist#0:0/pcm_s16le] [/bogus] bad")

    monkeypatch.setattr(AnalysisPipeline, "run", fail)
    result = _invoke(["analyze", str(tmp_path / "v.mp4")])
    assert result.exit_code == 1
    assert "[aist#0:0/pcm_s16le] [/bogus] bad" in result.output


def test_analyze_success_prints_table_and_saves_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not a real video")
    out_dir = tmp_path / "out"
    monkeypatch.setenv("CLIPPER_OUTPUT_DIR", str(out_dir))

    result = _invoke(["analyze", str(video)], AppContext(transcriber=_fake_transcriber))

    assert result.exit_code == 0, result.output
    assert "Top clips: clip.mp4" in result.output
    assert "Score" in result.output
    assert "0:00:00" in result.output
    assert "Saved:" in result.output
    saved = AnalysisResult.model_validate_json(
        (out_dir / "clip.json").read_text(encoding="utf-8")
    )
    assert saved.source == "clip.mp4"
    assert len(saved.candidates) >= 1


class StubDetector:
    def detect(self, transcript: Transcript) -> list[ClipCandidate]:
        return [
            ClipCandidate(
                start_seconds=1.0, end_seconds=2.0, score=0.5, reasons=("stub",)
            )
        ]


def test_analyze_uses_the_heuristic_by_default_and_records_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not a real video")
    out_dir = tmp_path / "out"
    monkeypatch.setenv("CLIPPER_OUTPUT_DIR", str(out_dir))
    names: list[DetectorName] = []

    def build(settings: Settings, name: DetectorName) -> StubDetector:
        names.append(name)
        return StubDetector()

    result = _invoke(
        ["analyze", str(video)],
        AppContext(transcriber=_fake_transcriber, detector=build),
    )

    assert result.exit_code == 0, result.output
    assert names == [DetectorName.HEURISTIC]
    assert "stub" in result.output
    assert "Detector: heuristic" in result.output
    saved = AnalysisResult.model_validate_json(
        (out_dir / "clip.json").read_text(encoding="utf-8")
    )
    assert saved.detector == "heuristic"


def test_analyze_bracketed_file_name_is_not_parsed_as_markup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # yt-dlp's default names end in "[<video id>]".
    video = tmp_path / "Title [oFbD0Ot7nQ8].mp4"
    video.write_bytes(b"not a real video")
    monkeypatch.setenv("CLIPPER_OUTPUT_DIR", str(tmp_path / "out"))

    result = _invoke(["analyze", str(video)], AppContext(transcriber=_fake_transcriber))

    assert result.exit_code == 0, result.output
    # rich may wrap the title or path at the space, so check the bracketed part.
    assert "[oFbD0Ot7nQ8].mp4" in result.output
    assert "[oFbD0Ot7nQ8].json" in result.output


def test_analyze_bracketed_reason_is_not_parsed_as_markup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BracketDetector:
        def detect(self, transcript: Transcript) -> list[ClipCandidate]:
            return [
                ClipCandidate(
                    start_seconds=1.0,
                    end_seconds=2.0,
                    score=0.5,
                    reasons=("[/bold] stray",),
                )
            ]

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not a real video")
    monkeypatch.setenv("CLIPPER_OUTPUT_DIR", str(tmp_path / "out"))

    result = _invoke(
        ["analyze", str(video)],
        AppContext(
            transcriber=_fake_transcriber,
            detector=lambda settings, name: BracketDetector(),
        ),
    )

    assert result.exit_code == 0, result.output
    assert "[/bold] stray" in result.output


@pytest.mark.parametrize("name", ["fake", "ollama"])
def test_analyze_rejects_an_unknown_detector(tmp_path: Path, name: str) -> None:
    result = _invoke(["analyze", str(tmp_path / "v.mp4"), "--detector", name])
    assert result.exit_code == 2
    assert "Invalid value" in result.output


def test_analyze_records_the_llm_detector_with_its_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not a real video")
    out_dir = tmp_path / "out"
    monkeypatch.setenv("CLIPPER_OUTPUT_DIR", str(out_dir))
    monkeypatch.setenv("CLIPPER_LLM_MODEL", "some-model:tag")
    names: list[DetectorName] = []

    def build(settings: Settings, name: DetectorName) -> StubDetector:
        names.append(name)
        return StubDetector()

    result = _invoke(
        ["analyze", str(video), "--detector", "llm"],
        AppContext(transcriber=_fake_transcriber, detector=build),
    )

    assert result.exit_code == 0, result.output
    assert names == [DetectorName.LLM]
    assert "Detector: llm:some-model:tag" in result.output
    saved = AnalysisResult.model_validate_json(
        (out_dir / "clip.json").read_text(encoding="utf-8")
    )
    assert saved.detector == "llm:some-model:tag"


def test_analyze_llm_with_an_empty_model_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not a real video")
    out_dir = tmp_path / "out"
    monkeypatch.setenv("CLIPPER_OUTPUT_DIR", str(out_dir))
    monkeypatch.setenv("CLIPPER_LLM_MODEL", "")
    transcribed: list[Path] = []

    class RecordingTranscriber(FakeTranscriber):
        def transcribe(self, media_path: Path) -> Transcript:
            transcribed.append(media_path)
            return super().transcribe(media_path)

    # The real detector builder, so its empty-model check runs.
    result = _invoke(
        ["analyze", str(video), "--detector", "llm"],
        AppContext(transcriber=lambda settings: RecordingTranscriber()),
    )

    assert result.exit_code == 1
    assert "Error: Set CLIPPER_LLM_MODEL" in result.output
    assert transcribed == []
    assert not out_dir.exists()


def test_analyze_detection_error_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailingDetector:
        def detect(self, transcript: Transcript) -> list[ClipCandidate]:
            raise DetectionError("Cannot reach Ollama at [url]")

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not a real video")
    out_dir = tmp_path / "out"
    monkeypatch.setenv("CLIPPER_OUTPUT_DIR", str(out_dir))

    result = _invoke(
        ["analyze", str(video)],
        AppContext(
            transcriber=_fake_transcriber,
            detector=lambda settings, name: FailingDetector(),
        ),
    )

    assert result.exit_code == 1
    assert "Error: Cannot reach Ollama at [url]" in result.output
    assert not out_dir.exists()


def test_build_detector_composes_the_llm_detector() -> None:
    settings = Settings(llm_model="some-model:tag")
    detector = build_detector(settings, DetectorName.LLM)
    assert isinstance(detector, LlmClipDetector)


def test_a_context_that_is_not_an_app_context_fails_loudly(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["analyze", str(tmp_path / "v.mp4")], obj="not a context"
    )
    assert isinstance(result.exception, TypeError)
    assert "Expected an AppContext, got str" in str(result.exception)


def test_the_default_context_composes_the_real_adapters() -> None:
    context = AppContext()
    settings = Settings()
    assert isinstance(context.transcriber(settings), CachingTranscriber)
    detector = context.detector(settings, DetectorName.HEURISTIC)
    assert isinstance(detector, HeuristicClipDetector)
    assert isinstance(context.renderer(settings), FfmpegClipRenderer)
    assert isinstance(context.probe(), FfprobeMediaProbe)
    assert isinstance(context.frames(), FfmpegFrameExtractor)
    assert isinstance(context.review_files(), JsonReviewFiles)
    assert isinstance(context.title_writer(settings), OllamaTitleWriter)


def test_build_title_writer_needs_a_model() -> None:
    with pytest.raises(TitleError, match="Set CLIPPER_LLM_MODEL .* --titles"):
        build_title_writer(Settings(llm_model=""))
