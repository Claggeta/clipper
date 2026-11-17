from datetime import date
from pathlib import Path

import pytest

from clipper.adapters.results.studio_files import load_studio_export, save_snapshot
from clipper.domain.errors import PerformanceError

TABLE = (
    "﻿Content,Video title,Video publish time,Duration,Views\n"
    'Total,,,,10\nabc,A Short ✈️,"Nov 14, 2026",27,10\n'
)


def export(tmp_path: Path, name: str = "Content 2026-10-20_2026-11-17 Chan") -> Path:
    folder = tmp_path / name
    folder.mkdir()
    (folder / "Table data.csv").write_text(TABLE, encoding="utf-8")
    (folder / "Totals.csv").write_text("Date,Views\n2026-11-17,10\n", encoding="utf-8")
    return folder


def test_load_studio_export_reads_the_table_and_the_folder_name(tmp_path: Path) -> None:
    loaded = load_studio_export(export(tmp_path))
    assert [(r.video_id, r.title, r.views) for r in loaded.rows] == [
        ("abc", "A Short ✈️", 10)
    ]
    assert (loaded.first_day, loaded.last_day, loaded.channel) == (
        date(2026, 10, 20),
        date(2026, 11, 17),
        "Chan",
    )


def test_a_renamed_folder_has_no_days(tmp_path: Path) -> None:
    loaded = load_studio_export(export(tmp_path, "mine"))
    assert loaded.last_day is None


def test_load_studio_export_names_the_file(tmp_path: Path) -> None:
    with pytest.raises(PerformanceError, match="No Table data.csv"):
        load_studio_export(tmp_path)
    (tmp_path / "Table data.csv").write_text("Content\nabc\n", encoding="utf-8")
    with pytest.raises(PerformanceError, match=r"Table data\.csv: missing column"):
        load_studio_export(tmp_path)


def test_save_snapshot_copies_the_files_and_replaces_an_older_one(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "perf" / "2026-11-17"
    snapshot.mkdir(parents=True)
    (snapshot / "Chart data.csv").write_text("old", encoding="utf-8")
    (snapshot / "notes.txt").write_text("mine", encoding="utf-8")
    written = save_snapshot(export(tmp_path), snapshot)
    assert [p.name for p in written] == ["Table data.csv", "Totals.csv"]
    assert (snapshot / "Table data.csv").read_text(encoding="utf-8") == TABLE
    assert not (snapshot / "Chart data.csv").exists()
    assert (snapshot / "notes.txt").exists()
