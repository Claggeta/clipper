import shutil
from pathlib import Path

from clipper.core.performance import (
    EXPORT_FILES,
    TABLE_FILE,
    parse_export_name,
    parse_table,
)
from clipper.domain.errors import PerformanceError
from clipper.domain.models import StudioExport


def load_studio_export(folder: Path) -> StudioExport:
    """Read a YouTube Studio export folder's table (ADR 0027, first export
    amendment), with the days and channel its name gives.

    Raises `PerformanceError` naming the file.
    """
    table = folder / TABLE_FILE
    if not table.is_file():
        raise PerformanceError(f"No {TABLE_FILE} in {folder}")
    try:
        text = table.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise PerformanceError(f"Cannot read {table}: {exc}") from exc
    try:
        rows = parse_table(text)
    except PerformanceError as exc:
        raise PerformanceError(f"{table}: {exc}") from exc
    named = parse_export_name(folder.name)
    if named is None:
        return StudioExport(rows=rows)
    first, last, channel = named
    return StudioExport(rows=rows, first_day=first, last_day=last, channel=channel)


def save_snapshot(folder: Path, snapshot: Path) -> list[Path]:
    """Copy the export's files, unchanged, into `snapshot`, after removing
    any a previous import of the same day left there (ADR 0027 decision 4).
    Returns the files written.

    Raises `PerformanceError` naming the file that cannot be written.
    """
    written: list[Path] = []
    try:
        snapshot.mkdir(parents=True, exist_ok=True)
        for name in EXPORT_FILES:
            (snapshot / name).unlink(missing_ok=True)
    except OSError as exc:
        raise PerformanceError(f"Cannot clear snapshot {snapshot}: {exc}") from exc
    for name in EXPORT_FILES:
        source = folder / name
        if not source.is_file():
            continue
        target = snapshot / name
        try:
            shutil.copy2(source, target)
        except OSError as exc:
            raise PerformanceError(f"Cannot write {target}: {exc}") from exc
        written.append(target)
    return written
