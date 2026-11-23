from pathlib import Path

import pytest

from clipper.adapters.results.channels_txt import load_channel_abouts, load_people
from clipper.domain.errors import ReviewError, TitleError

# Constructed files, not the maintainer's channels file.


def test_no_file_means_no_saved_about(tmp_path: Path) -> None:
    assert load_channel_abouts(tmp_path / "channels.txt") == {}


def test_reads_the_file_with_or_without_a_bom(tmp_path: Path) -> None:
    path = tmp_path / "channels.txt"
    path.write_bytes("﻿Lacy | Lacy (he/him)\n".encode())
    assert load_channel_abouts(path) == {"lacy": "Lacy (he/him)"}


def test_a_bad_line_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "channels.txt"
    path.write_text("lacy\n", encoding="utf-8")
    with pytest.raises(TitleError, match=r"channels\.txt: Line 1: write it as"):
        load_channel_abouts(path)


def test_a_file_that_is_not_utf8_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "channels.txt"
    path.write_bytes(b"lacy | \xff\n")
    with pytest.raises(TitleError, match="Cannot read channels file"):
        load_channel_abouts(path)


def test_no_people_file_means_no_names(tmp_path: Path) -> None:
    assert load_people(tmp_path / "people.txt") == ()


def test_reads_the_people_file(tmp_path: Path) -> None:
    path = tmp_path / "people.txt"
    path.write_text("# names\nRon\nJason\n", encoding="utf-8")
    assert load_people(path) == ("Ron", "Jason")


def test_a_bad_people_line_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "people.txt"
    path.write_text("Ron\nRon\n", encoding="utf-8")
    with pytest.raises(ReviewError, match=r"people\.txt: Line 2: 'Ron' is given twice"):
        load_people(path)


# Saved cam boxes per channel (ADR 0025, 12b amendment).


def test_no_cams_file_means_no_saved_boxes(tmp_path: Path) -> None:
    from clipper.adapters.results.channels_txt import load_channel_cams

    assert load_channel_cams(tmp_path / "cams.txt") == {}


def test_a_cam_box_is_saved_and_read_back(tmp_path: Path) -> None:
    from clipper.adapters.results.channels_txt import (
        load_channel_cams,
        save_channel_cam,
    )
    from clipper.domain.models import FacecamBox

    path = tmp_path / "data" / "cams.txt"
    box = FacecamBox(x=0.0, y=0.0, w=0.25, h=0.25)
    save_channel_cam(path, "Lacy", box)
    assert load_channel_cams(path) == {"lacy": box}
    save_channel_cam(path, "Lacy", None)
    assert load_channel_cams(path) == {}


def test_a_bad_cams_file_names_the_file_and_is_not_overwritten(tmp_path: Path) -> None:
    from clipper.adapters.results.channels_txt import save_channel_cam
    from clipper.domain.errors import FaceError
    from clipper.domain.models import FacecamBox

    path = tmp_path / "cams.txt"
    path.write_text("Lacy | nope\n", encoding="utf-8")
    with pytest.raises(FaceError, match=r"cams\.txt: Line 1: write it as"):
        save_channel_cam(path, "Ron", FacecamBox(x=0.0, y=0.0, w=0.2, h=0.2))
    assert path.read_text(encoding="utf-8") == "Lacy | nope\n"
