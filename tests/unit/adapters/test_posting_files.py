import logging
import os
from datetime import date
from pathlib import Path

import pytest

from clipper.adapters.results.manifest_json import save_manifest
from clipper.adapters.results.posting_files import (
    load_campaigns,
    load_drafts_folders,
    load_posts,
    save_posts,
)
from clipper.domain.errors import QueueError
from clipper.domain.models import PostLog, PostRecord, RenderManifest, VideoInfo

# Constructed files, not the maintainer's.


def _manifest(source: str) -> RenderManifest:
    return RenderManifest(
        source=source,
        analysis="a.json",
        detector=None,
        vertical=True,
        captions=True,
        titles=True,
        clips=(),
    )


def test_no_campaigns_file_means_no_campaigns(tmp_path: Path) -> None:
    assert load_campaigns(tmp_path / "campaigns.txt") == ()


def test_a_bad_campaigns_line_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "campaigns.txt"
    path.write_text("CORE | #CORE\n", encoding="utf-8")
    with pytest.raises(QueueError, match=r"campaigns\.txt: Line 1"):
        load_campaigns(path)


def test_posts_round_trip_and_no_file_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "posts.json"
    assert load_posts(path) == PostLog()
    log = PostLog(
        posts=(
            PostRecord(
                clip="v/c.mp4",
                url="https://youtu.be/abcDEF12345",
                video_id="abcDEF12345",
                posted=date(2026, 11, 12),
            ),
        )
    )
    save_posts(log, path)
    assert load_posts(path) == log


def test_a_bad_posts_file_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "posts.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(QueueError, match="Cannot read posts file"):
        load_posts(path)


def test_drafts_folders_come_newest_first_with_labels_and_channel(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    clips, labels, videos = tmp_path / "clips", tmp_path / "labels", tmp_path / "videos"
    old = save_manifest(_manifest("old.mp4"), clips / "old")
    new = save_manifest(_manifest("new.mp4"), clips / "new")
    os.utime(old, (1_000, 1_000))
    os.utime(new, (2_000, 2_000))
    (clips / "bad").mkdir()
    (clips / "bad" / "manifest.json").write_text("{", encoding="utf-8")
    videos.mkdir()
    (videos / "new.info.json").write_text(
        VideoInfo(
            platform="twitch", video_id="v", title="T", channel="Lacy"
        ).model_dump_json(),
        encoding="utf-8",
    )
    (labels / "picks").mkdir(parents=True)
    (labels / "picks" / "old.json").write_text(
        '{"source": "old.mp4", "labels": []}', encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING):
        folders = load_drafts_folders(clips, labels, videos)
    assert [(f.name, f.channel, f.labels is not None) for f in folders] == [
        ("new", "Lacy", False),
        ("old", None, True),
    ]
    assert "Queue skips" in caplog.text
