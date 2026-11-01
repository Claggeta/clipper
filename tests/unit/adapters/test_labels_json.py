import json
from pathlib import Path

import pytest

from clipper.adapters.results.labels_json import load_labels
from clipper.domain.errors import ClipperError, LabelsError

# The maintainer's labels for data/test_input.mp4 (times read while watching
# it, 2026-11-01). The notes were written from the transcript, not by the
# maintainer.
_LABELS = """{
  "source": "test_input.mp4",
  "labels": [
    { "start": "0:00", "end": "0:50", "note": "Make your bed: the opener" },
    { "start": "0:50", "end": "2:29", "note": "SEAL bed inspection" },
    { "start": "2:30", "end": "3:47", "note": "Darkest moment, power of hope" },
    { "start": "3:48", "end": "4:35", "note": "Ring the bell" }
  ]
}
"""


def _write(tmp_path: Path, text: str | bytes) -> Path:
    path = tmp_path / "labels.json"
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


def test_loads_labels(tmp_path: Path) -> None:
    video = load_labels(_write(tmp_path, _LABELS))

    assert video.source == "test_input.mp4"
    assert [(label.start_seconds, label.end_seconds) for label in video.labels] == [
        (0.0, 50.0),
        (50.0, 149.0),
        (150.0, 227.0),
        (228.0, 275.0),
    ]
    assert video.labels[3].note == "Ring the bell"


def test_errors_are_clipper_errors() -> None:
    assert issubclass(LabelsError, ClipperError)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(LabelsError, match="Labels file not found"):
        load_labels(tmp_path / "nope.json")


def test_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(LabelsError, match="Labels file not found"):
        load_labels(tmp_path)


def test_unreadable_file_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path, _LABELS)

    def deny(self: Path) -> bytes:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_bytes", deny)

    with pytest.raises(LabelsError, match="Cannot read labels file"):
        load_labels(path)


# The cases below use constructed input.


@pytest.mark.parametrize("content", ["", "{", "not json", b"\xff\xfe\x00"])
def test_invalid_json_raises(tmp_path: Path, content: str | bytes) -> None:
    with pytest.raises(LabelsError, match="Invalid JSON in labels file") as excinfo:
        load_labels(_write(tmp_path, content))

    assert "Invalid JSON:" not in str(excinfo.value)


@pytest.mark.parametrize("content", ["[]", "null", "3", "{}"])
def test_wrong_shape_raises(tmp_path: Path, content: str) -> None:
    with pytest.raises(LabelsError, match="is not a valid labels file"):
        load_labels(_write(tmp_path, content))


def test_plain_number_names_the_field(tmp_path: Path) -> None:
    data = json.loads(_LABELS)
    data["labels"][1]["start"] = 0.50

    with pytest.raises(LabelsError, match=r"labels\.1\.start: .*M:SS"):
        load_labels(_write(tmp_path, json.dumps(data)))


def test_schema_error_counts_the_rest(tmp_path: Path) -> None:
    data = json.loads(_LABELS)
    data["labels"][0]["start"] = "zero"
    data["labels"][2]["end"] = 227

    with pytest.raises(LabelsError, match=r"labels\.0\.start: .*\(\+1 more\)"):
        load_labels(_write(tmp_path, json.dumps(data)))


def test_overlap_is_reported(tmp_path: Path) -> None:
    data = json.loads(_LABELS)
    data["labels"][2]["start"] = "2:00"

    with pytest.raises(LabelsError, match="labels.2 starts before labels.1 ends"):
        load_labels(_write(tmp_path, json.dumps(data)))
