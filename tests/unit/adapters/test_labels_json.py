import json
import os
from pathlib import Path

import pytest

from clipper.adapters.results.labels_json import (
    check_labels_match,
    load_labels,
    load_links,
    save_labels,
)
from clipper.domain.errors import ClipperError, LabelsError
from clipper.domain.models import AnalysisResult, LabeledVideo, Transcript

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


# Writing labels and reading links files, with constructed input.


def test_saved_labels_load_back(tmp_path: Path) -> None:
    labeled = load_labels(_write(tmp_path, _LABELS))
    path = tmp_path / "out" / "copy.json"

    save_labels(labeled, path)

    assert load_labels(path) == labeled


def test_saved_labels_use_the_hand_written_format(tmp_path: Path) -> None:
    labeled = LabeledVideo.model_validate(
        {
            "source": "v1.mp4",
            "labels": [
                {"start": "4:35:45", "end": "4:36:45", "note": "Twitch clips: ✨"},
                {"start": "4:40:00.5", "end": "4:41:00"},
            ],
        }
    )
    path = tmp_path / "v1.json"

    save_labels(labeled, path)

    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"}\n")
    assert json.loads(raw.decode("utf-8")) == {
        "source": "v1.mp4",
        "labels": [
            {"start": "4:35:45", "end": "4:36:45", "note": "Twitch clips: ✨"},
            {"start": "4:40:00.5", "end": "4:41:00"},
        ],
    }
    assert "✨" in raw.decode("utf-8")


def test_saved_judged_labels_keep_verdicts_drafts_and_rejected(
    tmp_path: Path,
) -> None:
    # Constructed judged labels (ADR 0012, V5 amendment).
    data = {
        "source": "v1.mp4",
        "labels": [
            {
                "start": "1:39:11",
                "end": "1:39:55",
                "verdict": "fix",
                "draft": {"start": "1:39:11", "end": "1:39:49"},
                "note": "needs the reaction",
            },
            {"start": "2:10:00", "end": "2:10:40", "verdict": "added"},
            {"start": "5:24:04", "end": "5:24:49", "verdict": "post"},
        ],
        "rejected": [
            {"start": "0:12:00", "end": "0:12:45", "note": "nothing happening"},
            {"start": "0:12:10", "end": "0:12:55"},
        ],
    }
    labeled = LabeledVideo.model_validate(data)
    path = tmp_path / "v1.json"

    save_labels(labeled, path)

    assert json.loads(path.read_text(encoding="utf-8")) == data
    assert load_labels(path) == labeled


def test_save_labels_reports_a_write_failure(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a folder", encoding="utf-8")
    labeled = LabeledVideo(source="v1.mp4", labels=())
    with pytest.raises(LabelsError, match="Cannot write labels file"):
        save_labels(labeled, blocker / "v1.json")


def test_load_links_parses_the_file(tmp_path: Path) -> None:
    path = tmp_path / "v1.txt"
    path.write_bytes(
        b"\xef\xbb\xbf# saved with a BOM\r\n"
        b"https://clips.twitch.tv/A-a https://www.twitch.tv/videos/1?t=1m5s\r\n"
    )
    links = load_links(path)
    assert [(link.line, link.clip_id, link.end_seconds) for link in links] == [
        (2, "A-a", 65)
    ]


def test_load_links_errors_name_the_file(tmp_path: Path) -> None:
    path = tmp_path / "v1.txt"
    path.write_text("https://clips.twitch.tv/A-a\n", encoding="utf-8")
    with pytest.raises(LabelsError, match=r"v1\.txt line 1: expected a clip URL"):
        load_links(path)


def test_load_links_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(LabelsError, match="Links file not found"):
        load_links(tmp_path / "nope.txt")


def test_load_links_undecodable_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "v1.txt"
    path.write_bytes(b"\xff\xfe\xfa")
    with pytest.raises(LabelsError, match="Cannot read links file"):
        load_links(path)


# The source checks below use constructed input.


def _result(source: str) -> AnalysisResult:
    return AnalysisResult(
        source=source, transcript=Transcript(segments=()), candidates=()
    )


def test_matching_source_passes() -> None:
    labeled = LabeledVideo(source="talk.mp4", labels=())
    check_labels_match(labeled, _result("talk.mp4"))


def test_mismatched_source_raises() -> None:
    labeled = LabeledVideo(source="a.mp4", labels=())
    with pytest.raises(LabelsError) as excinfo:
        check_labels_match(labeled, _result("b.mp4"))

    assert str(excinfo.value) == (
        "Labels are for 'a.mp4' but the analysis result is for 'b.mp4'"
    )


@pytest.mark.skipif(os.name != "nt", reason="case-insensitive filesystems only")
def test_case_differences_pass_on_windows() -> None:
    check_labels_match(LabeledVideo(source="Talk.MP4", labels=()), _result("talk.mp4"))


@pytest.mark.skipif(os.name == "nt", reason="case-sensitive filesystems only")
def test_case_differences_fail_elsewhere() -> None:
    with pytest.raises(LabelsError):
        check_labels_match(
            LabeledVideo(source="Talk.MP4", labels=()), _result("talk.mp4")
        )
