from pathlib import Path

import pytest
from typer.testing import CliRunner

from clipper.adapters.transcription.fake import FakeTranscriber
from clipper.cli import main as cli_main
from clipper.cli.main import app
from clipper.core.pipeline import AnalysisPipeline
from clipper.domain.errors import AudioExtractionError
from clipper.domain.models import AnalysisResult


def test_missing_file_shows_friendly_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["analyze", str(tmp_path / "nope.mp4")])
    assert result.exit_code == 1
    assert "Error" in result.output


def test_error_with_bracketed_text_is_not_parsed_as_markup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(self: AnalysisPipeline, video: Path) -> object:
        raise AudioExtractionError("[aist#0:0/pcm_s16le] [/bogus] bad")

    monkeypatch.setattr(AnalysisPipeline, "run", fail)
    result = CliRunner().invoke(app, ["analyze", str(tmp_path / "v.mp4")])
    assert result.exit_code == 1
    assert "[aist#0:0/pcm_s16le] [/bogus] bad" in result.output


def test_analyze_success_prints_table_and_saves_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not a real video")
    out_dir = tmp_path / "out"
    monkeypatch.setenv("CLIPPER_OUTPUT_DIR", str(out_dir))
    monkeypatch.setattr(
        cli_main, "_build_transcriber", lambda settings: FakeTranscriber()
    )

    result = CliRunner().invoke(app, ["analyze", str(video)])

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
