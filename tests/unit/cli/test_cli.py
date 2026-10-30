from pathlib import Path

import pytest
from typer.testing import CliRunner

from clipper.cli.main import app
from clipper.core.pipeline import AnalysisPipeline
from clipper.domain.errors import AudioExtractionError


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
