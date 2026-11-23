from pathlib import Path

from clipper.core.channels import (
    parse_channel_abouts,
    parse_channel_cams,
    parse_people,
    set_channel_cam,
)
from clipper.domain.errors import FaceError, ReviewError, TitleError
from clipper.domain.models import FacecamBox


def load_channel_abouts(path: Path) -> dict[str, str]:
    """Read the channels file; no file means no saved about text.

    See docs/decisions/0018 (saved about amendment). Raises `TitleError`,
    naming the file, if it cannot be read or a line is not valid.
    """
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise TitleError(f"Cannot read channels file {path}: {exc}") from exc
    try:
        return parse_channel_abouts(text)
    except TitleError as exc:
        raise TitleError(f"{path}: {exc}") from exc


def load_channel_cams(path: Path) -> dict[str, FacecamBox]:
    """Read the cams file; no file means no saved cam boxes.

    See docs/decisions/0025 (12b amendment). Raises `FaceError`, naming the
    file, if it cannot be read or a line is not valid.
    """
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise FaceError(f"Cannot read cams file {path}: {exc}") from exc
    try:
        return parse_channel_cams(text)
    except FaceError as exc:
        raise FaceError(f"{path}: {exc}") from exc


def save_channel_cam(path: Path, channel: str, box: FacecamBox | None) -> None:
    """Write the channel's cam box into the cams file, or take it out for
    no box; a missing file is started. Raises `FaceError` on a write
    failure or a file that is not valid."""
    load_channel_cams(path)
    try:
        text = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(set_channel_cam(text, channel, box), encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise FaceError(f"Cannot write cams file {path}: {exc}") from exc


def load_people(path: Path) -> tuple[str, ...]:
    """Read the people file; no file means no names to offer.

    See docs/decisions/0024 (rating amendment). Raises `ReviewError`, naming
    the file, if it cannot be read or a line is not valid.
    """
    if not path.is_file():
        return ()
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise ReviewError(f"Cannot read people file {path}: {exc}") from exc
    try:
        return parse_people(text)
    except ReviewError as exc:
        raise ReviewError(f"{path}: {exc}") from exc
