from pathlib import Path

from typer.testing import CliRunner

from clipper.cli.main import app


def test_missing_file_shows_friendly_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["analyze", str(tmp_path / "nope.mp4")])
    assert result.exit_code == 1
    assert "Error" in result.output
