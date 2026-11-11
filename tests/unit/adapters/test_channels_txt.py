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
