from pathlib import Path

from clipper.core.channels import parse_channel_abouts
from clipper.domain.errors import TitleError


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
