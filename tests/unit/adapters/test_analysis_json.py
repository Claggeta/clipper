import json
import os
from pathlib import Path

import pytest

from clipper.adapters.results.analysis_json import (
    check_source_matches,
    load_analysis_result,
)
from clipper.domain.errors import AnalysisResultError, ClipperError
from clipper.domain.models import AnalysisResult

# Excerpt of real `clipper analyze` output (data/output/test_input.json): the
# first and last transcript segments and the top candidate, indent=2 as saved.
_REAL = """{
  "source": "test_input.mp4",
  "transcript": {
    "segments": [
      {
        "start_seconds": 1.36,
        "end_seconds": 9.43,
        "text": "If you want to change the world, start off by making your bed."
      },
      {
        "start_seconds": 267.5,
        "end_seconds": 273.8,
        "text": "don't ever, ever ring the bell."
      }
    ]
  },
  "candidates": [
    {
      "start_seconds": 32.43,
      "end_seconds": 73.78,
      "score": 0.25951229343006854,
      "reasons": [
        "strong words: never",
        "fast pace (3.1 words/sec)"
      ]
    }
  ]
}
"""


def _write(tmp_path: Path, text: str | bytes) -> Path:
    path = tmp_path / "result.json"
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


def _payload(**overrides: object) -> str:
    data = json.loads(_REAL)
    data.update(overrides)
    return json.dumps(data)


def test_loads_real_output(tmp_path: Path) -> None:
    result = load_analysis_result(_write(tmp_path, _REAL))

    assert result.source == "test_input.mp4"
    assert len(result.transcript.segments) == 2
    assert result.candidates[0].score == 0.25951229343006854
    assert result.candidates[0].reasons[1] == "fast pace (3.1 words/sec)"


def test_round_trips_model_dump(tmp_path: Path) -> None:
    original = load_analysis_result(_write(tmp_path, _REAL))
    path = _write(tmp_path, original.model_dump_json(indent=2))

    assert load_analysis_result(path) == original


def test_errors_are_clipper_errors() -> None:
    assert issubclass(AnalysisResultError, ClipperError)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(AnalysisResultError, match="Analysis result not found"):
        load_analysis_result(tmp_path / "nope.json")


def test_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(AnalysisResultError, match="Analysis result not found"):
        load_analysis_result(tmp_path)


def test_unreadable_file_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path, _REAL)

    def deny(self: Path) -> bytes:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_bytes", deny)

    with pytest.raises(AnalysisResultError, match="Cannot read analysis result"):
        load_analysis_result(path)


# The cases below use constructed input, not observed `analyze` output.


@pytest.mark.parametrize("content", ["", "{", "not json", b"\xff\xfe\x00"])
def test_invalid_json_raises(tmp_path: Path, content: str | bytes) -> None:
    with pytest.raises(AnalysisResultError, match="Invalid JSON in analysis result"):
        load_analysis_result(_write(tmp_path, content))


@pytest.mark.parametrize("content", ["[]", "null", "3", "{}"])
def test_wrong_shape_raises(tmp_path: Path, content: str) -> None:
    with pytest.raises(AnalysisResultError, match="is not a valid analysis result"):
        load_analysis_result(_write(tmp_path, content))


def test_schema_error_names_the_field_and_counts_the_rest(tmp_path: Path) -> None:
    data = json.loads(_REAL)
    data["candidates"][0]["score"] = 2
    data["candidates"][0]["reasons"] = []
    path = _write(tmp_path, json.dumps(data))

    with pytest.raises(AnalysisResultError) as excinfo:
        load_analysis_result(path)

    message = str(excinfo.value)
    assert "candidates.0.score" in message
    assert "(+1 more)" in message


def test_missing_key_names_the_field(tmp_path: Path) -> None:
    data = json.loads(_REAL)
    del data["transcript"]

    with pytest.raises(AnalysisResultError, match="transcript: Field required"):
        load_analysis_result(_write(tmp_path, json.dumps(data)))


def _result(source: str) -> AnalysisResult:
    return AnalysisResult.model_validate_json(_payload(source=source))


def test_matching_name_passes() -> None:
    check_source_matches(_result("talk.mp4"), Path("some/dir/talk.mp4"))


def test_mismatched_name_raises() -> None:
    with pytest.raises(AnalysisResultError) as excinfo:
        check_source_matches(_result("a.mp4"), Path("b.mp4"))

    assert str(excinfo.value) == (
        "Analysis result is for 'a.mp4' but the video is 'b.mp4'"
    )


def test_directory_part_of_the_video_path_is_ignored() -> None:
    check_source_matches(_result("talk.mp4"), Path("elsewhere") / "talk.mp4")


@pytest.mark.skipif(os.name != "nt", reason="case-insensitive filesystems only")
def test_case_differences_pass_on_windows() -> None:
    check_source_matches(_result("Talk.MP4"), Path("talk.mp4"))


@pytest.mark.skipif(os.name == "nt", reason="case-sensitive filesystems only")
def test_case_differences_fail_elsewhere() -> None:
    with pytest.raises(AnalysisResultError):
        check_source_matches(_result("Talk.MP4"), Path("talk.mp4"))
