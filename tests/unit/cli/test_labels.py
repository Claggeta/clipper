import json
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from clipper.adapters.download.ytdlp import YtDlpClipLookup
from clipper.cli.context import AppContext
from clipper.cli.main import app
from clipper.core.config import Settings
from clipper.domain.errors import DownloadError
from clipper.domain.models import ClipInfo

# Constructed clip IDs, VOD number and titles; nothing here calls Twitch.
_VOD = "https://www.twitch.tv/videos/1234567890"
_LINKS = (
    "# constructed links file\n"
    f"https://www.twitch.tv/ch/clip/Late-c?range=7d {_VOD}?t=1h0m0s\n"
    f"https://clips.twitch.tv/First-a {_VOD}?t=1m40s\n"
    f"https://clips.twitch.tv/Second-b {_VOD}?t=2m10s\n"
)
_INFOS = {
    "Late-c": ClipInfo(title="Late one", duration_seconds=60),
    "First-a": ClipInfo(title="First", duration_seconds=30),  # 1:10-1:40
    "Second-b": ClipInfo(title="Second", duration_seconds=40),  # 1:30-2:10
}


class FakeClipLookup:
    def __init__(self, error_on: str | None = None) -> None:
        self.calls: list[str] = []
        self._error_on = error_on

    def lookup(self, clip_id: str) -> ClipInfo:
        self.calls.append(clip_id)
        if clip_id == self._error_on:
            raise DownloadError("yt-dlp failed (exit 1): ERROR: clip gone")
        return _INFOS[clip_id]


@pytest.fixture
def labels_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "labels"
    monkeypatch.setenv("CLIPPER_LABELS_DIR", str(folder))
    monkeypatch.setenv("COLUMNS", "200")
    return folder


def _links(tmp_path: Path, text: str = _LINKS) -> Path:
    path = tmp_path / "links.txt"
    path.write_text(text, encoding="utf-8")
    return path


def _labels(links: Path, lookup: FakeClipLookup, *flags: str) -> Result:
    context = AppContext(clip_lookup=lambda settings: lookup)
    return CliRunner().invoke(app, ["labels", str(links), *flags], obj=context)


def test_labels_writes_merged_labels_for_the_vod(
    tmp_path: Path, labels_dir: Path
) -> None:
    lookup = FakeClipLookup()
    result = _labels(_links(tmp_path), lookup)

    assert result.exit_code == 0, result.output
    assert lookup.calls == ["Late-c", "First-a", "Second-b"]
    target = labels_dir / "v1234567890.json"
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "source": "v1234567890.mp4",
        "labels": [
            {
                "start": "0:01:10",
                "end": "0:02:10",
                "note": "Twitch clips: First; Second",
            },
            {"start": "0:59:00", "end": "1:00:00", "note": "Twitch clips: Late one"},
        ],
    }
    assert "Clip 1 of 3: Late one (60 s, ends 1:00:00)" in result.output
    assert "Clips read: 3" in result.output
    assert "Labels after merging: 2" in result.output
    assert "Minutes covered: 2.0" in result.output
    assert f"Saved: {target}" in result.output


def test_an_existing_labels_file_is_kept_without_force(
    tmp_path: Path, labels_dir: Path
) -> None:
    labels_dir.mkdir()
    target = labels_dir / "v1234567890.json"
    target.write_text("hand-written", encoding="utf-8")
    lookup = FakeClipLookup()

    result = _labels(_links(tmp_path), lookup)

    assert result.exit_code == 1
    assert "already exists; pass --force to replace it" in result.output
    assert lookup.calls == []
    assert target.read_text(encoding="utf-8") == "hand-written"


def test_force_replaces_an_existing_labels_file(
    tmp_path: Path, labels_dir: Path
) -> None:
    labels_dir.mkdir()
    target = labels_dir / "v1234567890.json"
    target.write_text("old", encoding="utf-8")

    result = _labels(_links(tmp_path), FakeClipLookup(), "--force")

    assert result.exit_code == 0, result.output
    assert json.loads(target.read_text(encoding="utf-8"))["source"] == (
        "v1234567890.mp4"
    )


def test_a_bad_line_fails_before_any_lookup(tmp_path: Path, labels_dir: Path) -> None:
    text = (
        _LINKS + "https://clips.twitch.tv/Other-d https://www.twitch.tv/videos/9?t=5s\n"
    )
    lookup = FakeClipLookup()

    result = _labels(_links(tmp_path, text), lookup)

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "line 5: VOD v9 differs from v1234567890 on line 2" in result.output
    assert lookup.calls == []
    assert not labels_dir.exists()


def test_a_lookup_failure_writes_nothing(tmp_path: Path, labels_dir: Path) -> None:
    result = _labels(_links(tmp_path), FakeClipLookup(error_on="First-a"))

    assert result.exit_code == 1
    assert "Error: yt-dlp failed (exit 1): ERROR: clip gone" in result.output
    assert not (labels_dir / "v1234567890.json").exists()


def test_a_missing_links_file_is_an_error(tmp_path: Path, labels_dir: Path) -> None:
    result = _labels(tmp_path / "nope.txt", FakeClipLookup())
    assert result.exit_code == 1
    assert "Error: Links file not found" in result.output


def test_help_states_the_terms() -> None:
    result = CliRunner().invoke(app, ["labels", "--help"])
    assert result.exit_code == 0
    assert "against Twitch's terms of service" in result.output


def test_the_default_context_builds_the_ytdlp_clip_lookup() -> None:
    assert isinstance(AppContext().clip_lookup(Settings()), YtDlpClipLookup)
